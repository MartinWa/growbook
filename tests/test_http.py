from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import OperationalError
from django.test import Client
from django.utils import timezone, translation

from core.media import private_path
from diary.models import DiaryEntry
from diary.services import create_entry

pytestmark = pytest.mark.django_db(transaction=True)


def entry(user, uploads=(), body="Synthetic note"):
    return create_entry(
        author=user, date=date(2026, 5, 9), body=body, uploads=uploads, submission_key=uuid4()
    ).entry


def post_data(**overrides):
    return {
        "date": "2026-05-09",
        "body": "A synthetic note",
        "submission_key": str(uuid4()),
        **overrides,
    }


@pytest.mark.parametrize(
    "path", ["/", "/capture/", "/entries/1/", "/photos/1/", "/photos/1/original/", "/operator/"]
)
def test_anonymous_pages_require_login(client, path):
    response = client.get(path)
    assert response.status_code == 302
    assert "login" in response.url
    assert "no-store" in response["Cache-Control"]


def test_login_and_language_selection_are_public(client):
    assert client.get("/accounts/login/").status_code == 200
    response = client.post("/accounts/language/", {"language": "en", "next": "/accounts/login/"})
    assert response.status_code == 302
    assert response.cookies["django_language"].value == "en"


def test_favicon_is_public_and_self_hosted(client):
    response = client.get("/accounts/login/")
    assert (
        '<link rel="icon" type="image/svg+xml" href="/static/icons/favicon.svg">' in response.text
    )
    response = client.get("/favicon.ico")
    assert response.status_code == 301
    assert response.url == "/static/icons/favicon.svg"
    response = client.get(response.url)
    assert response.status_code == 200
    assert response["Content-Type"] == "image/svg+xml"
    response.close()
    assert client.head("/favicon.ico").status_code == 301
    assert client.post("/favicon.ico").status_code == 405


def test_swedish_default_does_not_follow_browser_language(client):
    response = client.get("/accounts/login/", HTTP_ACCEPT_LANGUAGE="en-US,en")
    assert '<html lang="sv">' in response.text
    assert "Logga in" in response.text


def test_english_locale_is_available(client, user):
    client.force_login(user)
    client.cookies["django_language"] = "en"
    response = client.get("/capture/")
    assert '<html lang="en">' in response.text
    assert "Save observation" in response.text


def test_empty_diary_copy_describes_supported_capture(client, user):
    client.force_login(user)
    response = client.get("/")
    assert "Börja med det du vill dokumentera." in response.text
    client.cookies["django_language"] = "en"
    response = client.get("/")
    assert "A photo, a short note, or both. Start with what you want to record." in response.text
    assert "You can add details later" not in response.text


def test_garden_local_today_is_default(client, user, monkeypatch):
    monkeypatch.setattr(timezone, "now", lambda: datetime(2026, 1, 2, tzinfo=UTC))
    client.force_login(user)
    response = client.get("/capture/")
    assert response.context["form"]["date"].value() == date(2026, 1, 2)


def test_capture_normal_form_redirects_after_save(client, user):
    client.force_login(user)
    response = client.post("/capture/", post_data(author=99999))
    assert response.status_code == 302
    stored = DiaryEntry.objects.get()
    assert stored.author_id == user.pk
    assert response.url == stored.get_absolute_url()


def test_camera_and_library_are_saved_in_one_entry(client, user, image_upload):
    client.force_login(user)
    response = client.post(
        "/capture/",
        post_data(body="", camera=image_upload(), library=[image_upload(), image_upload()]),
    )
    assert response.status_code == 302
    assert DiaryEntry.objects.get().photos.count() == 3


def test_htmx_save_uses_success_redirect(client, user):
    client.force_login(user)
    response = client.post("/capture/", post_data(), HTTP_HX_REQUEST="true")
    assert response.status_code == 204
    assert response["HX-Redirect"] == DiaryEntry.objects.get().get_absolute_url()


def test_inline_capture_returns_a_receipt_and_a_new_key_with_retry_safety(client, user):
    client.force_login(user)
    client.cookies["django_language"] = "en"
    data = post_data(capture_mode="quick")
    response = client.post("/capture/", data, HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert response["HX-Retarget"] == "#quick-receipt"
    assert "HX-Redirect" not in response
    assert "Observation saved." in response.text
    assert data["submission_key"] not in response.text
    retry = client.post("/capture/", data, HTTP_HX_REQUEST="true")
    assert retry.status_code == 200
    assert "Observation already saved." in retry.text
    assert DiaryEntry.objects.count() == 1


def test_inline_capture_without_javascript_redirects_to_the_filtered_diary(client, user):
    client.force_login(user)
    response = client.post(
        "/capture/",
        post_data(capture_mode="quick", filter_query="q=synthetic&year=2026&page=2"),
    )
    assert response.status_code == 302
    assert response.url == f"/?q=synthetic&year=2026#entry-{DiaryEntry.objects.get().pk}"


def test_quick_add_state_omits_pagination(client, user):
    entry(user)
    client.force_login(user)
    response = client.get("/?q=synthetic&year=2026&page=2")
    assert "page" not in response.context["filter_query"]
    assert 'name="filter_query" value="q=synthetic&amp;year=2026"' in response.text


def test_invalid_diary_filter_renders_inline_field_feedback(client, user):
    client.force_login(user)
    response = client.get("/?year=invalid")
    assert response.status_code == 422
    assert 'id="id_year_errors"' in response.text
    assert 'data-filter-field-errors="year"' in response.text


def test_photo_picker_uses_configured_limits(client, user, settings):
    settings.PHOTO_MAX_COUNT = 3
    settings.PHOTO_MAX_FILE_BYTES = 2 * 1024 * 1024
    settings.PHOTO_MAX_TOTAL_BYTES = 5 * 1024 * 1024
    client.force_login(user)
    client.cookies["django_language"] = "en"
    response = client.get("/")
    assert 'data-photo-limit="3"' in response.text
    assert "Up to 3 photos" in response.text
    assert "2 MiB each, 5 MiB per upload" in response.text


def test_inline_capture_failure_without_javascript_keeps_draft_key_and_filters(client, user):
    client.force_login(user)
    data = post_data(
        capture_mode="quick", date="invalid", body="Keep this draft", filter_query="q=synthetic"
    )
    response = client.post("/capture/", data)
    assert response.status_code == 422
    form = response.context["capture_form"]
    assert form["submission_key"].value() == data["submission_key"]
    assert form["body"].value() == "Keep this draft"
    assert response.context["form"]["q"].value() == "synthetic"
    assert 'name="capture_mode" value="quick"' in response.text
    assert not DiaryEntry.objects.exists()


def test_diary_fragment_contains_results_and_chips_without_replacing_capture(client, user):
    record = entry(user, body="Synthetic tomato note")
    client.force_login(user)
    response = client.get(
        "/?q=tomato&year=2026&page=1",
        HTTP_HX_REQUEST="true",
        HTTP_HX_TARGET="diary-results",
    )
    assert response.status_code == 200
    assert f'id="entry-{record.pk}"' in response.text
    assert 'id="capture-form"' not in response.text
    assert "<script" not in response.text
    chips = response.context["active_filters"]
    assert [chip["url"] for chip in chips] == ["/?year=2026", "/?q=tomato"]
    invalid = client.get("/?year=invalid", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="diary-results")
    assert invalid.status_code == 422
    assert 'id="diary-results"' in invalid.text


def test_operator_link_is_only_in_staff_account_menus(client, user, staff_user):
    client.force_login(user)
    assert 'class="menu-link" href="/operator/"' not in client.get("/").text
    client.force_login(staff_user)
    assert 'class="menu-link" href="/operator/"' in client.get("/").text


def test_empty_post_has_visible_error(client, user):
    client.force_login(user)
    response = client.post("/capture/", {"date": "2026-05-09", "submission_key": str(uuid4())})
    assert response.status_code == 422
    assert response.context["form"].errors
    assert DiaryEntry.objects.count() == 0


def test_htmx_validation_only_replaces_errors_not_the_form(client, user):
    client.force_login(user)
    response = client.post("/capture/", post_data(date="invalid"), HTTP_HX_REQUEST="true")
    assert response.status_code == 422
    assert "error-summary" in response.text
    assert '<form id="capture-form"' not in response.text
    assert "<script" not in response.text


def test_non_javascript_failure_preserves_note_date_and_key(client, user):
    client.force_login(user)
    data = post_data(body="Keep this note", library=SimpleUploadedFile("bad.jpg", b"invalid"))
    response = client.post("/capture/", data)
    assert response.status_code == 422
    assert response.context["form"]["body"].value() == data["body"]
    assert response.context["form"]["date"].value() == data["date"]
    assert response.context["form"]["submission_key"].value() == data["submission_key"]
    with translation.override("en"):
        assert DiaryEntry.objects.count() == 0


def test_request_byte_limit_cannot_silently_drop_last_file(client, user, image_upload, settings):
    client.force_login(user)
    settings.PHOTO_MAX_FILE_BYTES = 700
    response = client.post(
        "/capture/",
        post_data(
            library=[
                image_upload(),
                SimpleUploadedFile("huge.jpg", b"x" * 701),
            ]
        ),
    )
    assert response.status_code == 422
    assert DiaryEntry.objects.count() == 0


def test_unexpected_file_field_cannot_be_silently_ignored(client, user, image_upload):
    client.force_login(user)
    response = client.post("/capture/", post_data(unexpected=image_upload()))
    assert response.status_code == 422
    assert DiaryEntry.objects.count() == 0


def test_photo_request_count_and_total_bytes_are_bounded(client, user, image_upload, settings):
    client.force_login(user)
    settings.PHOTO_MAX_COUNT = 1
    response = client.post("/capture/", post_data(library=[image_upload(), image_upload()]))
    assert response.status_code == 422
    settings.PHOTO_MAX_COUNT = 10
    settings.PHOTO_MAX_TOTAL_BYTES = 100
    response = client.post("/capture/", post_data(library=image_upload()))
    assert response.status_code == 422
    assert DiaryEntry.objects.count() == 0


def test_native_count_limit_has_visible_validation_and_no_partial_save(client, user, image_upload):
    client.force_login(user)
    response = client.post("/capture/", post_data(library=[image_upload() for _ in range(11)]))
    assert response.status_code == 422
    assert response.context["form"].errors
    assert DiaryEntry.objects.count() == 0


def test_htmx_conflicting_retry_returns_visible_conflict(client, user):
    client.force_login(user)
    data = post_data()
    assert client.post("/capture/", data).status_code == 302
    data["body"] = "Different content"
    response = client.post("/capture/", data, HTTP_HX_REQUEST="true")
    assert response.status_code == 409
    assert DiaryEntry.objects.count() == 1


def test_lost_http_save_response_is_retryable_without_duplicate(client, user, monkeypatch):
    from diary import views

    client.force_login(user)
    real_create = views.create_entry
    calls = 0

    def lost_response(**kwargs):
        nonlocal calls
        result = real_create(**kwargs)
        calls += 1
        if calls == 1:
            raise OperationalError("Synthetic response lost after commit")
        return result

    monkeypatch.setattr(views, "create_entry", lost_response)
    data = post_data()
    assert client.post("/capture/", data, HTTP_HX_REQUEST="true").status_code == 503
    assert client.post("/capture/", data, HTTP_HX_REQUEST="true").status_code == 204
    assert DiaryEntry.objects.count() == 1


def test_expired_session_htmx_does_not_navigate_away_from_draft(client):
    response = client.post("/capture/", post_data(), HTTP_HX_REQUEST="true")
    assert response.status_code == 401
    assert "HX-Redirect" not in response


def test_csrf_is_enforced_and_failure_is_actionable(user):
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    response = client.post("/capture/", post_data())
    assert response.status_code == 403
    assert "no-store" in response["Cache-Control"]
    assert "unsafe-inline" not in response["Content-Security-Policy"]
    assert DiaryEntry.objects.count() == 0
    assert 'target="_blank"' in response.text


@pytest.mark.parametrize("variant", ["original", "display", "thumbnail"])
def test_private_photo_variants(client, user, image_upload, variant):
    stored = entry(user, [image_upload()])
    photo = stored.photos.get()
    response = client.get(photo.variant_url(variant))
    assert response.status_code == 302
    client.force_login(user)
    response = client.get(photo.variant_url(variant))
    assert response.status_code == 200
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in response["Cache-Control"]
    assert response["Content-Type"] == ("image/jpeg" if variant == "original" else "image/webp")
    assert response["Content-Disposition"].startswith(
        "attachment" if variant == "original" else "inline"
    )
    assert (
        b"".join(response.streaming_content)
        == private_path(getattr(photo, variant).name).read_bytes()
    )


@pytest.mark.parametrize("variant", ["display", "thumbnail"])
def test_photo_navigation_redirects_to_private_html_viewer(client, user, image_upload, variant):
    photo = entry(user, [image_upload()]).photos.get()
    client.force_login(user)
    response = client.get(photo.variant_url(variant), HTTP_SEC_FETCH_DEST="document")
    assert response.status_code == 302
    assert response.url == photo.get_absolute_url()
    assert "Sec-Fetch-Dest" in response["Vary"]
    assert "no-store" in response["Cache-Control"]
    response = client.get(response.url)
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    assert f'<img src="{photo.display_url}"' in response.text
    assert f'href="{photo.entry.get_absolute_url()}"' in response.text
    assert f'href="{photo.original_url}"' in response.text
    assert "Tillbaka till observationen" in response.text
    assert "no-store" in response["Cache-Control"]
    assert "style-src 'self'" in response["Content-Security-Policy"]
    assert "unsafe-inline" not in response["Content-Security-Policy"]
    assert "unsafe-eval" not in response["Content-Security-Policy"]
    assert client.post(photo.get_absolute_url()).status_code == 405


def test_diary_photo_links_use_html_viewer(client, user, image_upload):
    stored = entry(user, [image_upload()]).photos.get()
    client.force_login(user)
    for path in ("/", stored.entry.get_absolute_url()):
        response = client.get(path)
        assert f'<a href="{stored.get_absolute_url()}">' in response.text
        assert f'<a href="{stored.display_url}">' not in response.text
    client.cookies["django_language"] = "en"
    response = client.get(stored.get_absolute_url())
    assert "Back to observation" in response.text
    assert "Download original" in response.text
    assert client.get("/photos/999999/").status_code == 404


def test_photo_viewer_escapes_caption_and_does_not_accept_an_external_return_url(
    client, user, image_upload, monkeypatch
):
    from core import views

    photo = entry(user, [image_upload()]).photos.get()
    photo.caption = '<script>alert("x")</script>'
    # Exercise escaping without adding an unsupported caption writer.
    monkeypatch.setattr(views, "get_object_or_404", lambda *args, **kwargs: photo)
    client.force_login(user)
    response = client.get(photo.get_absolute_url(), {"next": "https://example.com/"})
    assert response.status_code == 200
    assert "&lt;script&gt;" in response.text
    assert '<script>alert("x")</script>' not in response.text
    assert f'href="{photo.entry.get_absolute_url()}"' in response.text
    assert "https://example.com/" not in response.text


def test_photo_image_requests_and_original_navigation_still_return_files(
    client, user, image_upload
):
    photo = entry(user, [image_upload()]).photos.get()
    client.force_login(user)
    for variant, destination in (
        ("display", "image"),
        ("thumbnail", "image"),
        ("original", "document"),
    ):
        response = client.get(photo.variant_url(variant), HTTP_SEC_FETCH_DEST=destination)
        assert response.status_code == 200
        assert response["Content-Type"] == ("image/jpeg" if variant == "original" else "image/webp")
        assert response["Content-Disposition"].startswith(
            "attachment" if variant == "original" else "inline"
        )
        assert (
            b"".join(response.streaming_content)
            == private_path(getattr(photo, variant).name).read_bytes()
        )


def test_no_public_media_alias_or_arbitrary_variant(client, user, image_upload):
    photo = entry(user, [image_upload()]).photos.get()
    client.force_login(user)
    assert client.get(f"/media/{photo.original.name}").status_code == 404
    assert client.get(f"/photos/{photo.pk}/../../db.sqlite3/").status_code == 404
    assert client.get(f"/photos/{photo.pk}/secrets/").status_code == 404


def test_other_active_members_can_read_shared_garden(client, user, image_upload, django_user_model):
    photo = entry(user, [image_upload()]).photos.get()
    other = django_user_model.objects.create_user(username="second-gardener", password="synthetic")
    client.force_login(other)
    response = client.get(photo.display_url)
    assert response.status_code == 200
    response.close()
    assert client.get(photo.get_absolute_url()).status_code == 200
    other.is_active = False
    other.save()
    assert client.get(photo.get_absolute_url()).status_code == 302


def test_deactivation_invalidates_login(client, user):
    client.force_login(user)
    user.is_active = False
    user.save()
    assert client.get("/").status_code == 302


def test_logout_requires_post(client, user):
    client.force_login(user)
    assert client.get("/accounts/logout/").status_code == 405
    assert client.post("/accounts/logout/").status_code == 302
    assert client.get("/").status_code == 302


def test_user_content_is_escaped_and_csp_is_restrictive(client, user):
    stored = entry(user, body="<script>alert('x')</script>\nÄrtor")
    client.force_login(user)
    response = client.get(stored.get_absolute_url())
    assert "&lt;script&gt;" in response.text
    assert "<script>alert" not in response.text
    csp = response["Content-Security-Policy"]
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    assert "frame-ancestors 'none'" in csp
    assert '"historyCacheSize":0' in response.text


def test_diary_is_chronological_with_stable_paging(client, user):
    for number in range(22):
        entry(user, body=f"Observation {number}")
    client.force_login(user)
    page1 = client.get("/").context["page_obj"]
    page2 = client.get("/?page=2").context["page_obj"]
    assert len(page1) == 20 and len(page2) == 2
    assert set(item.pk for item in page1).isdisjoint(item.pk for item in page2)
    assert page1[0].pk > page1[-1].pk > page2[0].pk


def test_no_public_signup_endpoint(client):
    assert client.get("/accounts/signup/").status_code == 404


def test_anonymous_author_is_rejected():
    with pytest.raises(PermissionDenied):
        create_entry(
            author=AnonymousUser(),
            date=date(2026, 5, 9),
            body="note",
            uploads=[],
            submission_key=uuid4(),
        )
