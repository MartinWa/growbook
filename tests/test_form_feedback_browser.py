"""Save feedback owns one request and restores the form's previous control states."""

import re

import pytest
from browser_helpers import browser_page, login
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def page(live_server, user, browser_endpoint):
    with browser_page(browser_endpoint, viewport={"width": 1280, "height": 900}) as page:
        login(page, live_server, user, "en")
        yield page


@pytest.fixture(params=["/", "/garden/areas/new/"], ids=["capture", "register"])
def feedback_form(page, live_server, request):
    capture = request.param == "/"
    if not capture:
        page.goto(live_server.url + request.param)
    form = page.locator("#capture-form" if capture else "#register-form")
    form.locator("#id_title" if capture else "#id_name").fill("Synthetic feedback heading")
    form.locator("#id_body" if capture else "#id_notes").fill("Synthetic retained writing")
    return form


def choose_photo(form, image_upload):
    picker = form.locator("[data-photo-picker]").first
    file_input = picker.locator("input[type=file]:not([capture])")
    file_input.set_input_files(
        {"name": "synthetic.jpg", "mimeType": "image/jpeg", "buffer": image_upload().read()}
    )
    return file_input


def fail_save(route):
    route.fulfill(
        status=503,
        content_type="text/html",
        body='<div class="error-summary">Synthetic temporary failure</div>',
    )


def test_failed_save_restores_disabled_and_readonly_controls_before_retry(
    page, live_server, feedback_form, image_upload
):
    form = feedback_form
    capture = form.get_attribute("id") == "capture-form"
    file_input = choose_photo(form, image_upload)
    key = form.locator("[name=submission_key]").input_value()
    form.evaluate(
        """form => {
            const heading = form.querySelector('#id_title, #id_name');
            heading.readOnly = true;
            form.querySelector('[data-photo-chooser]').disabled = true;
            form.querySelector('[data-photo-previews] button').disabled = true;
            form.querySelector('select').disabled = true;
            const tagTrigger = form.querySelector('[data-tag-trigger]');
            if (tagTrigger) tagTrigger.disabled = true;
            window.originalControls = [...form.querySelectorAll('button, input, select, textarea')]
                .map(input => [input, input.disabled, input.readOnly]);
        }"""
    )
    pending = []
    url = live_server.url + form.get_attribute("action")
    page.route(url, lambda route: pending.append(route))
    save = form.locator(".form-actions button[type=submit]")
    save.click()
    expect(form).to_have_attribute("aria-busy", "true")
    expect(file_input).to_be_disabled()
    expect(form.locator("#id_body, #id_notes")).not_to_be_editable()
    assert len(pending) == 1
    fail_save(pending[0])
    errors = page.locator(form.get_attribute("hx-target"))
    expect(errors).to_contain_text("Synthetic temporary failure")
    expect(errors).to_be_focused()
    expect(form).not_to_have_attribute("aria-busy", "true")
    assert page.evaluate(
        "originalControls.every(([input, disabled, readOnly]) => "
        "input.disabled === disabled && input.readOnly === readOnly)"
    )
    expect(file_input).to_be_enabled()
    assert file_input.evaluate("input => input.files.length") == 1
    expect(form.locator("[name=submission_key]")).to_have_value(key)
    expect(form.locator("#id_body, #id_notes")).to_have_value("Synthetic retained writing")
    page.unroute(url)
    save.click()
    if capture:
        expect(page.locator("#quick-receipt")).to_contain_text("Observation saved")
        expect(file_input).to_be_enabled()
    else:
        expect(page).to_have_url(re.compile(r"/garden/areas/\d+/$"))
        expect(page.locator(".register-photos figure")).to_have_count(1)


def test_cancelled_save_keeps_controls_idle_and_existing_feedback(
    page, live_server, feedback_form, image_upload
):
    form = feedback_form
    file_input = choose_photo(form, image_upload)
    errors = page.locator(form.get_attribute("hx-target"))
    errors.evaluate("errors => errors.textContent = 'Synthetic existing feedback'")
    form.evaluate(
        "form => form.addEventListener('htmx:beforeRequest', "
        "event => event.preventDefault(), {once: true})"
    )
    pending = []
    page.route(live_server.url + form.get_attribute("action"), lambda route: pending.append(route))
    save = form.locator(".form-actions button[type=submit]")
    save.click()
    assert pending == []
    expect(form).not_to_have_attribute("aria-busy", "true")
    expect(save).to_be_enabled()
    expect(file_input).to_be_enabled()
    expect(form.locator("[data-photo-chooser]").first).to_be_enabled()
    expect(form.locator("#id_body, #id_notes")).to_be_editable()
    expect(form.locator("progress")).to_be_hidden()
    expect(errors).to_have_text("Synthetic existing feedback")
    save.click()
    expect(form).to_have_attribute("aria-busy", "true")
    assert len(pending) == 1
    fail_save(pending[0])
    expect(errors).to_contain_text("Synthetic temporary failure")


def test_foreign_and_previous_request_events_cannot_change_pending_save_feedback(
    page, live_server, feedback_form, image_upload
):
    form = feedback_form
    file_input = choose_photo(form, image_upload)
    key = form.locator("[name=submission_key]").input_value()
    form.evaluate(
        """form => {
            window.formRequests = [];
            form.addEventListener('htmx:beforeSend', event => formRequests.push(event.detail.xhr));
        }"""
    )
    pending = []
    page.route(live_server.url + form.get_attribute("action"), lambda route: pending.append(route))
    save = form.locator(".form-actions button[type=submit]")
    errors = page.locator(form.get_attribute("hx-target"))
    save.click()
    expect(form).to_have_attribute("aria-busy", "true")
    assert len(pending) == 1
    fail_save(pending[0])
    expect(errors).to_contain_text("Synthetic temporary failure")
    save.click()
    expect(form).to_have_attribute("aria-busy", "true")
    assert len(pending) == 2
    form.locator("#id_title, #id_name").focus()
    ignored = form.evaluate(
        """form => {
            const errors = form.querySelector(form.getAttribute('hx-target'));
            const origin = form.querySelector('#id_title, #id_name');
            const current = formRequests[1];
            const previous = formRequests[0];
            current.upload.dispatchEvent(new ProgressEvent('progress', {
                lengthComputable: true, loaded: 25, total: 100
            }));
            previous.upload.dispatchEvent(new ProgressEvent('progress', {
                lengthComputable: true, loaded: 100, total: 100
            }));
            const config = {
                elt: origin, target: errors, parameters: {csrfmiddlewaretoken: 'foreign'}
            };
            origin.dispatchEvent(new CustomEvent('htmx:configRequest', {
                bubbles: true, detail: config
            }));
            const unchanged = [];
            for (const requestOrigin of [origin, form]) {
                const detail = {
                    elt: requestOrigin, requestConfig: {elt: requestOrigin}, xhr: previous,
                    target: errors, shouldSwap: false, isError: true
                };
                for (const name of ['htmx:beforeSwap', 'htmx:afterSwap', 'htmx:afterRequest',
                                    'htmx:sendError', 'htmx:timeout']) {
                    const target = name.endsWith('Swap') ? errors : requestOrigin;
                    target.dispatchEvent(new CustomEvent(name, {
                        bubbles: true, detail: {...detail}
                    }));
                }
                const swap = new CustomEvent('htmx:beforeSwap', {bubbles: true, detail});
                errors.dispatchEvent(swap);
                unchanged.push(!detail.shouldSwap && detail.isError);
            }
            origin.dispatchEvent(new CustomEvent('htmx:xhr:progress', {
                bubbles: true,
                detail: {elt: origin, lengthComputable: true, loaded: 100, total: 100}
            }));
            return config.parameters.csrfmiddlewaretoken === 'foreign' && unchanged.every(Boolean);
        }"""
    )
    assert ignored
    expect(form).to_have_attribute("aria-busy", "true")
    expect(save).to_be_disabled()
    expect(file_input).to_be_disabled()
    expect(form.locator("#id_title, #id_name")).to_be_focused()
    expect(errors).to_be_empty()
    expect(form.locator("progress")).to_have_js_property("value", 25)
    assert form.locator(
        "#capture-status, [data-register-status]"
    ).text_content() == form.get_attribute("data-uploading")
    form.evaluate(
        """() => formRequests[1].upload.dispatchEvent(new ProgressEvent('progress', {
            lengthComputable: true, loaded: 100, total: 100
        }))"""
    )
    expect(form.locator("progress")).to_have_js_property("value", 100)
    assert form.locator(
        "#capture-status, [data-register-status]"
    ).text_content() == form.get_attribute("data-processing")
    fail_save(pending[1])
    expect(errors).to_contain_text("Synthetic temporary failure")
    expect(save).to_be_enabled()
    expect(file_input).to_be_enabled()
    expect(form.locator("progress")).to_be_hidden()
    expect(form.locator("[name=submission_key]")).to_have_value(key)
    assert file_input.evaluate("input => input.files.length") == 1
