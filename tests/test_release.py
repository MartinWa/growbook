import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from tools import publish_release

ROOT = Path(__file__).resolve().parents[1]


def release(tag, *, draft=False, prerelease=False):
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease}


def git(repository, environment, *arguments):
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.fixture
def release_repository(tmp_path):
    if not shutil.which("git"):
        pytest.skip("Release guard integration checks require Git.")
    repository = tmp_path / "repository"
    repository.mkdir()
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="Synthetic release tester",
        GIT_AUTHOR_EMAIL="release@example.invalid",
        GIT_COMMITTER_NAME="Synthetic release tester",
        GIT_COMMITTER_EMAIL="release@example.invalid",
        EVENT_NAME="push",
        TAG_CREATED="true",
        RELEASE_TAG="0.1.3",
        GITHUB_REPOSITORY="Example/Growbook",
        GITHUB_OUTPUT=(tmp_path / "output").as_posix(),
        GH_CALLS=(tmp_path / "gh-calls").as_posix(),
        CI_CONCLUSION="success",
    )
    git(repository, environment, "init", "--initial-branch=main")
    (repository / "pyproject.toml").write_text('[project]\nversion = "0.0.0"\n', encoding="utf-8")
    (repository / "uv.lock").write_text(
        'version = 1\n[[package]]\nname = "growbook"\nversion = "0.0.0"\n', encoding="utf-8"
    )
    git(repository, environment, "add", "pyproject.toml", "uv.lock")
    git(repository, environment, "commit", "-m", "Synthetic release commit")
    git(repository, environment, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repository, environment, "tag", "0.1.3")
    return repository, environment


def run_release_guard(repository, environment):
    # Load the workflow helper in a fresh process; real Git, mocked GitHub, no publication.
    script = """
import importlib.util
import os
import sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("publish_release", sys.argv[1])
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
original = helper.subprocess.run
def run(arguments, **options):
    if arguments[0] == "gh":
        with Path(os.environ["GH_CALLS"]).open("a", encoding="utf-8") as output:
            output.write(" ".join(arguments) + "\\n")
        if arguments[1:3] == ("run", "list"):
            return helper.subprocess.CompletedProcess(arguments, 0, os.environ["CI_CONCLUSION"])
        if arguments[1] == "api":
            return helper.subprocess.CompletedProcess(arguments, 0, "[[]]")
        raise AssertionError("GitHub publication must never run in this test")
    return original(arguments, **options)
helper.subprocess.run = run
sys.argv = ["publish_release.py", "--check"]
helper.main()
"""
    return subprocess.run(
        [sys.executable, "-c", script, str(ROOT / "tools" / "publish_release.py")],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )


@pytest.fixture(params=["pwsh", "powershell"])
def tag_repository(release_repository, request):
    powershell = shutil.which(request.param)
    if not powershell:
        pytest.skip(f"Release tag integration checks require {request.param}.")
    repository, environment = release_repository
    tools = repository / "tools"
    tools.mkdir()
    shutil.copyfile(ROOT / "tools" / "tag_release.ps1", tools / "tag_release.ps1")
    git(repository, environment, "add", "tools/tag_release.ps1")
    git(repository, environment, "commit", "-m", "Synthetic tag helper")
    return repository, environment, powershell


def run_tag_script(tag_repository, *arguments):
    repository, environment, powershell = tag_repository
    return subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(repository / "tools" / "tag_release.ps1"),
            *arguments,
        ],
        cwd=repository.parent,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_tag_script_increments_highest_numeric_patch_and_creates_annotated_tag(tag_repository):
    repository, environment, _ = tag_repository
    for tag in ("0.1.9", "v0.1.10", "2.0.0-rc.1", "unrelated", "01.2.0"):
        git(repository, environment, "tag", tag)
    result = run_tag_script(tag_repository)
    assert result.returncode == 0, result.stderr
    assert (
        git(repository, environment, "cat-file", "-t", "refs/tags/0.1.11").stdout.strip() == "tag"
    )
    assert (
        git(repository, environment, "rev-parse", "0.1.11^{commit}").stdout
        == git(repository, environment, "rev-parse", "HEAD").stdout
    )
    assert (
        git(
            repository, environment, "for-each-ref", "--format=%(contents)", "refs/tags/0.1.11"
        ).stdout.strip()
        == "Release 0.1.11"
    )


def test_tag_script_preview_leaves_tags_unchanged_and_does_not_push(tag_repository):
    repository, environment, _ = tag_repository
    before = git(repository, environment, "show-ref", "--tags").stdout
    result = run_tag_script(tag_repository, "-Push", "-WhatIf")
    assert result.returncode == 0, result.stderr
    assert "0.1.4" in result.stdout
    assert git(repository, environment, "show-ref", "--tags").stdout == before


@pytest.mark.parametrize("version", ["0.2.0", "0.1.2"])
def test_tag_script_accepts_explicit_version_including_maintenance_release(tag_repository, version):
    repository, environment, _ = tag_repository
    result = run_tag_script(tag_repository, "-Version", version)
    assert result.returncode == 0, result.stderr
    assert git(repository, environment, "tag", "--list").stdout.splitlines() == sorted(
        ["0.1.3", version]
    )


@pytest.mark.parametrize("version", ["v0.2.0", "0.2.0-rc.1", "01.2.0", "0.1.3"])
def test_tag_script_rejects_invalid_or_existing_versions(tag_repository, version):
    repository, environment, _ = tag_repository
    before = git(repository, environment, "show-ref", "--tags").stdout
    result = run_tag_script(tag_repository, "-Version", version)
    assert result.returncode != 0
    assert git(repository, environment, "show-ref", "--tags").stdout == before


def test_tag_script_requires_explicit_first_version(tag_repository):
    repository, environment, _ = tag_repository
    git(repository, environment, "tag", "-d", "0.1.3")
    result = run_tag_script(tag_repository)
    assert result.returncode != 0
    assert "Use -Version X.Y.Z" in result.stderr
    assert git(repository, environment, "tag", "--list").stdout == ""
    result = run_tag_script(tag_repository, "-Version", "0.1.0")
    assert result.returncode == 0, result.stderr
    assert git(repository, environment, "tag", "--list").stdout.strip() == "0.1.0"


@pytest.mark.parametrize("state", ["dirty", "branch"])
def test_tag_script_requires_clean_main_checkout(tag_repository, state):
    repository, environment, _ = tag_repository
    if state == "dirty":
        (repository / "uncommitted.txt").write_text("Synthetic change", encoding="utf-8")
    else:
        git(repository, environment, "switch", "-c", "synthetic-feature")
    before = git(repository, environment, "show-ref", "--tags").stdout
    result = run_tag_script(tag_repository)
    assert result.returncode != 0
    assert git(repository, environment, "show-ref", "--tags").stdout == before


@pytest.mark.parametrize("follow_tags", [False, True])
def test_tag_script_pushes_only_the_new_tag_to_local_remote(tag_repository, follow_tags):
    repository, environment, _ = tag_repository
    remote = repository.parent / "origin.git"
    git(repository.parent, environment, "init", "--bare", str(remote))
    git(repository, environment, "remote", "add", "origin", str(remote))
    git(repository, environment, "push", "origin", "main")
    git(repository, environment, "tag", "work-in-progress")
    git(repository, environment, "tag", "-a", "0.1.2", "-m", "Pending synthetic release")
    git(repository, environment, "config", "push.followTags", str(follow_tags).lower())
    result = run_tag_script(tag_repository, "-Push")
    assert result.returncode == 0, result.stderr
    assert git(remote, environment, "tag", "--list").stdout.strip() == "0.1.4"
    assert (
        git(remote, environment, "rev-parse", "0.1.4^{commit}").stdout
        == git(repository, environment, "rev-parse", "HEAD").stdout
    )


def test_tag_script_reports_failed_push_and_preserves_local_tag(tag_repository):
    repository, environment, _ = tag_repository
    result = run_tag_script(tag_repository, "-Push")
    assert result.returncode != 0
    assert "git push --no-follow-tags origin refs/tags/0.1.4" in result.stderr
    assert git(repository, environment, "tag", "--list").stdout.splitlines() == ["0.1.3", "0.1.4"]


@pytest.mark.parametrize("annotated", [False, True])
def test_tag_guard_accepts_tested_main_with_lightweight_or_annotated_tags(
    release_repository, annotated
):
    repository, environment = release_repository
    if annotated:
        git(repository, environment, "tag", "-d", "0.1.3")
        git(repository, environment, "tag", "-a", "0.1.3", "-m", "Synthetic annotated release")
    result = run_release_guard(repository, environment)
    assert result.returncode == 0, result.stderr
    assert Path(environment["GITHUB_OUTPUT"]).read_text(encoding="utf-8") == (
        "image=ghcr.io/example/growbook\nversion=0.1.3\n"
    )
    assert "--branch main --event push" in Path(environment["GH_CALLS"]).read_text(encoding="utf-8")


def test_manual_stable_release_still_passes_the_guard(release_repository):
    repository, environment = release_repository
    environment.update(EVENT_NAME="release", TAG_CREATED="")
    result = run_release_guard(repository, environment)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("conclusion", ["", "failure", "cancelled"])
def test_tag_guard_requires_successful_main_push_ci(release_repository, conclusion):
    repository, environment = release_repository
    environment["CI_CONCLUSION"] = conclusion
    result = run_release_guard(repository, environment)
    assert result.returncode != 0
    assert "successful CI push run on main" in result.stderr
    assert not Path(environment["GITHUB_OUTPUT"]).exists()


def test_tag_guard_rejects_an_existing_tag_update(release_repository):
    repository, environment = release_repository
    environment["TAG_CREATED"] = "false"
    result = run_release_guard(repository, environment)
    assert result.returncode != 0
    assert "do not move or overwrite an existing tag" in result.stderr
    assert not Path(environment["GH_CALLS"]).exists()


@pytest.mark.parametrize("tag", ["v0.1.3", "0.1.3-rc.1", "01.1.3"])
def test_tag_guard_rejects_unsupported_tag_names(release_repository, tag):
    repository, environment = release_repository
    environment["RELEASE_TAG"] = tag
    result = run_release_guard(repository, environment)
    assert result.returncode != 0
    assert not Path(environment["GH_CALLS"]).exists()


def test_tag_guard_rejects_a_tag_that_does_not_match_the_triggering_commit(release_repository):
    repository, environment = release_repository
    git(repository, environment, "commit", "--allow-empty", "-m", "Later synthetic commit")
    result = run_release_guard(repository, environment)
    assert result.returncode != 0
    assert "no longer points at the triggering commit" in result.stderr
    assert not Path(environment["GH_CALLS"]).exists()


def test_tag_guard_rejects_a_commit_outside_main(release_repository):
    repository, environment = release_repository
    git(repository, environment, "commit", "--allow-empty", "-m", "Synthetic unmerged commit")
    git(repository, environment, "tag", "-d", "0.1.3")
    git(repository, environment, "tag", "0.1.3")
    result = run_release_guard(repository, environment)
    assert result.returncode != 0
    assert not Path(environment["GH_CALLS"]).exists()


def test_tag_is_the_release_version_without_changing_project_or_lock_metadata(release_repository):
    repository, environment = release_repository
    original = {name: (repository / name).read_bytes() for name in ("pyproject.toml", "uv.lock")}
    git(repository, environment, "tag", "0.1.4")
    environment["RELEASE_TAG"] = "0.1.4"
    result = run_release_guard(repository, environment)
    assert result.returncode == 0, result.stderr
    assert Path(environment["GITHUB_OUTPUT"]).read_text(encoding="utf-8") == (
        "image=ghcr.io/example/growbook\nversion=0.1.4\n"
    )
    for name, contents in original.items():
        assert (repository / name).read_bytes() == contents


@pytest.mark.parametrize("tag", ["0.1.3", "1.0.0", "10.20.300"])
def test_stable_release_tags_are_unprefixed_numeric_versions(tag):
    assert publish_release.version_number(tag) == tuple(map(int, tag.split(".")))


@pytest.mark.parametrize("tag", ["v0.1.3", "0.1", "0.1.3-rc.1", "0.1.3+build", "01.1.3", "0.1.3\n"])
def test_invalid_release_tags_are_rejected_before_any_github_call(tag, monkeypatch):
    run = Mock()
    monkeypatch.setattr(publish_release.subprocess, "run", run)
    with pytest.raises(ValueError, match="X.Y.Z"):
        publish_release.publish_release("example/growbook", tag)
    run.assert_not_called()


def test_new_release_is_created_with_notes_and_an_existing_tag():
    arguments, latest = publish_release.release_plan("0.1.3", [release("0.1.2")])
    assert latest is True
    assert arguments == [
        "create",
        "0.1.3",
        "--verify-tag",
        "--title",
        "0.1.3",
        "--generate-notes",
        "--latest=true",
    ]


def test_first_release_can_be_latest():
    arguments, latest = publish_release.release_plan("1.0.0", [])
    assert latest is True
    assert arguments[0] == "create"


def test_draft_release_is_published_without_overwriting_its_notes():
    arguments, latest = publish_release.release_plan(
        "0.1.3", [release("0.1.2"), release("0.1.3", draft=True)]
    )
    assert latest is True
    assert arguments == ["edit", "0.1.3", "--draft=false", "--verify-tag", "--latest=true"]


def test_published_release_is_left_unchanged():
    assert publish_release.release_plan("0.1.3", [release("0.1.3")]) == ([], True)


@pytest.mark.parametrize("draft", [False, True])
def test_existing_prerelease_is_not_silently_published_as_stable(draft):
    with pytest.raises(ValueError, match="prerelease"):
        publish_release.release_plan("0.1.3", [release("0.1.3", draft=draft, prerelease=True)])


@pytest.mark.parametrize("higher", ["0.1.10", "v0.1.10", "1.0.0"])
def test_older_release_cannot_replace_latest_even_with_legacy_tags(higher):
    arguments, latest = publish_release.release_plan("0.1.3", [release(higher)])
    assert latest is False
    assert "--latest=false" in arguments
    assert "--latest=true" not in arguments


def test_older_published_release_rerun_does_not_promote_latest():
    assert publish_release.release_plan("0.1.3", [release("0.1.3"), release("0.1.10")]) == (
        [],
        False,
    )


def test_drafts_prereleases_and_unrelated_tags_do_not_block_latest():
    releases = [
        release("2.0.0", draft=True),
        release("3.0.0-rc.1", prerelease=True),
        release("4.0.0", prerelease=True),
        release("unrelated-tag"),
    ]
    arguments, latest = publish_release.release_plan("0.1.3", releases)
    assert latest is True
    assert "--latest=true" in arguments


def test_paginated_github_releases_are_all_considered(monkeypatch):
    run = Mock(
        side_effect=[
            subprocess.CompletedProcess(
                [], 0, json.dumps([[release("0.1.2")], [release("1.0.0")]])
            ),
            subprocess.CompletedProcess([], 0),
        ]
    )
    monkeypatch.setattr(publish_release.subprocess, "run", run)
    assert publish_release.publish_release("example/growbook", "0.1.3") is False
    run.assert_any_call(
        ["gh", "api", "--paginate", "--slurp", "repos/example/growbook/releases"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert run.call_args.args[0] == [
        "gh",
        "release",
        "create",
        "0.1.3",
        "--verify-tag",
        "--title",
        "0.1.3",
        "--generate-notes",
        "--latest=false",
        "--repo",
        "example/growbook",
    ]


def test_check_mode_does_not_create_a_release(monkeypatch):
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "[[]]"))
    monkeypatch.setattr(publish_release.subprocess, "run", run)
    assert publish_release.publish_release("example/growbook", "0.1.3", check_only=True) is True
    assert run.call_count == 1


def test_github_api_failure_is_not_treated_as_a_missing_release(monkeypatch):
    run = Mock(side_effect=subprocess.CalledProcessError(1, ["gh", "api"]))
    monkeypatch.setattr(publish_release.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        publish_release.publish_release("example/growbook", "0.1.3")
    assert run.call_count == 1


def test_release_creation_failure_does_not_produce_a_latest_output(monkeypatch):
    run = Mock(
        side_effect=[
            subprocess.CompletedProcess([], 0, "[[]]"),
            subprocess.CalledProcessError(1, ["gh", "release", "create"]),
        ]
    )
    monkeypatch.setattr(publish_release.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        publish_release.publish_release("example/growbook", "0.1.3")


def test_entry_point_writes_the_latest_decision(monkeypatch, tmp_path):
    output = tmp_path / "output"
    publish = Mock(return_value=False)
    monkeypatch.setattr(publish_release, "publish_release", publish)
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/growbook")
    monkeypatch.setenv("RELEASE_TAG", "0.1.3")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(sys, "argv", ["publish_release.py"])
    publish_release.main()
    publish.assert_called_once_with("example/growbook", "0.1.3", check_only=False)
    assert output.read_text(encoding="utf-8") == "latest=false\n"


def test_check_entry_point_validates_source_and_writes_image_outputs(monkeypatch, tmp_path):
    output = tmp_path / "output"
    publish = Mock(return_value=True)
    validate = Mock()
    monkeypatch.setattr(publish_release, "publish_release", publish)
    monkeypatch.setattr(publish_release, "validate_source", validate)
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/growbook")
    monkeypatch.setenv("RELEASE_TAG", "0.1.3")
    monkeypatch.setenv("EVENT_NAME", "push")
    monkeypatch.setenv("TAG_CREATED", "true")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(sys, "argv", ["publish_release.py", "--check"])
    publish_release.main()
    validate.assert_called_once_with("0.1.3", event_name="push", tag_created="true")
    publish.assert_called_once_with("example/growbook", "0.1.3", check_only=True)
    assert output.read_text(encoding="utf-8") == ("image=ghcr.io/example/growbook\nversion=0.1.3\n")


def test_release_workflow_publishes_images_before_creating_the_release():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert 'tags: ["[0-9]*.[0-9]*.[0-9]*"]' in workflow
    assert "!github.event.deleted" in workflow
    assert "contents: write" in workflow
    assert "ref: ${{ github.sha }}" in workflow
    assert "TAG_CREATED: ${{ github.event.created }}" in workflow
    assert "org.opencontainers.image.version=${{ steps.release.outputs.version }}" in workflow
    assert "GROWBOOK_VERSION=${{ steps.release.outputs.version }}" in workflow
    assert (
        "tags: ${{ steps.release.outputs.image }}:${{ steps.release.outputs.version }}" in workflow
    )
    assert workflow.index("run: python3 tools/publish_release.py --check") < workflow.index(
        "- name: Publish the tested image"
    )
    assert workflow.index("- name: Attest the image SBOM") < workflow.index(
        "- name: Create or publish the GitHub Release"
    )
    assert workflow.index("- name: Create or publish the GitHub Release") < workflow.index(
        "- name: Update latest for the current stable release"
    )
    assert "if: ${{ steps.github-release.outputs.latest == 'true' }}" in workflow
