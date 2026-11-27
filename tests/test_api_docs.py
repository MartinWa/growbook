"""The public OpenAPI schema endpoint and the Swagger UI documentation page."""

import json

import pytest
from django.urls import reverse

from api.schema import render_document

pytestmark = pytest.mark.django_db(transaction=True)

API = "/api/v1"


def test_schema_document_is_served_as_json_without_authentication(client):
    response = client.get(f"{API}/schema/")

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("application/json")
    assert response.json() == json.loads(render_document())


def test_schema_document_answers_writes_with_a_json_405(client):
    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)(f"{API}/schema/")
        assert response.status_code == 405, method
        assert response.headers["Content-Type"].startswith("application/json")
        assert response.json() == {"detail": f'Method "{method.upper()}" not allowed.'}


def test_schema_path_is_documented_as_public_and_read_only():
    document = json.loads(render_document())

    operation = document["paths"]["/schema/"]["get"]
    assert operation["security"] == []
    assert set(operation["responses"]) == {"200", "405"}


def test_docs_page_is_public_standalone_and_self_hosted(client):
    response = client.get(reverse("api-docs"))

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/html")
    html = response.content.decode("utf-8")
    # A bare documentation page: the Swagger UI, no site branding or navigation chrome.
    assert 'id="api-docs-swagger"' in html
    assert "site-header" not in html
    assert "app-footer" not in html
    assert "<header" not in html
    assert f"{API}/schema/" in html
    assert "vendor/swagger-ui-5.36.0/swagger-ui-bundle.js" in html
    assert "vendor/swagger-ui-5.36.0/swagger-ui.css" in html
    # No runtime CDN: every asset is served by this application itself.
    for cdn in ("unpkg.com", "cdn.jsdelivr.net", "cdnjs.cloudflare.com", "validator.swagger.io"):
        assert cdn not in html
    csp = response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp


def test_docs_page_answers_writes_with_405(client):
    assert client.post(reverse("api-docs")).status_code == 405


def test_api_token_page_links_to_the_documentation(client, user):
    client.force_login(user)

    response = client.get(reverse("api-tokens"))

    assert response.status_code == 200
    html = response.content.decode("utf-8")
    assert reverse("api-docs") in html
    assert "hash and an eight-character prefix" not in html
