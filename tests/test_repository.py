"""Repository-level consistency checks for deployment, release and contribution files."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_default_compose_publishes_no_host_ports_and_hardens_the_container():
    text = (ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "ports:" not in text
    assert "external: true" in text
    assert "read_only: true" in text
    assert "cap_drop" in text
    assert "no-new-privileges:true" in text
    assert "init: true" in text
    # Waitress has no graceful drain, so a longer stop period would promise what the
    # server cannot honour.
    assert "stop_grace_period" not in text


def test_host_proxy_override_binds_loopback_only():
    text = (ROOT / "compose.host-port.yaml").read_text(encoding="utf-8")

    assert '"127.0.0.1:${GROWBOOK_HOST_PORT:-8000}:8000"' in text
    assert "0.0.0.0:" not in text
    assert "Never bind this to 0.0.0.0" in text


def test_example_environment_and_deployment_guide_cover_the_optional_settings():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    deployment = (ROOT / "docs/deployment.md").read_text(encoding="utf-8")

    assert "GROWBOOK_SECRET_KEY_FILE" in example
    assert "| `GROWBOOK_SECRET_KEY_FILE` |" in deployment
    assert "| `GROWBOOK_HOST_PORT` |" in deployment
    assert "compose.host-port.yaml" in deployment
    for placeholder in ("GROWBOOK_SECRET_KEY=", "GROWBOOK_ALLOWED_HOSTS=", "APP_DATA_PATH="):
        assert placeholder in example


def test_contributor_agreement_is_documented_and_does_not_use_an_archived_action():
    agreement = (ROOT / "CLA.md").read_text(encoding="utf-8")
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    workflows = [
        path.read_text(encoding="utf-8") for path in (ROOT / ".github/workflows").glob("*.yml")
    ]

    assert "You retain ownership of the copyright" in agreement
    assert "right to relicense" in agreement
    assert "## 6. Corporate contributions" in agreement
    assert "the owner of the `MartinWa/growbook` GitHub repository" in agreement
    assert "the laws of Sweden" in agreement
    assert "cla-assistant.io" in contributing
    assert "docs/development.md#test-and-check" in contributing
    assert not any("contributor-assistant/github-action" in text for text in workflows)
    assert not (ROOT / "signatures").exists()
    assert "CONTRIBUTING.md" in readme
    assert "CLA.md" in readme
