"""Create a stable GitHub Release after its image has passed publishing checks."""

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

STABLE_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
CI_EVENTS = ("push", "workflow_dispatch")
CI_POLL_SECONDS = 20
CI_WAIT_SECONDS = 900
# A rate limit or a network blip must not decide a release; a persistent failure must.
CI_LOOKUP_ATTEMPTS = 3


def version_number(tag, *, legacy=False):
    # Earlier releases used a v prefix; accept it only when comparing history.
    match = STABLE_VERSION.fullmatch(tag.removeprefix("v") if legacy else tag)
    return tuple(map(int, match.groups())) if match else None


def read_command(*arguments):
    return subprocess.run(arguments, check=True, capture_output=True, text=True).stdout.strip()


def read_ci_run(commit, event):
    """Return the latest CI run for a commit and event, or None before that run exists."""
    runs = json.loads(
        read_command(
            "gh",
            "run",
            "list",
            "--workflow",
            "ci.yml",
            "--commit",
            commit,
            "--branch",
            "main",
            "--event",
            event,
            "--limit",
            "1",
            "--json",
            "conclusion",
        )
    )
    return runs[0] if runs else None


def read_ci_runs(commit):
    """Return the latest CI run per event, omitting events without a run yet."""
    runs = {}
    for event in CI_EVENTS:
        run = read_ci_run(commit, event)
        if run:
            runs[event] = run
    return runs


def wait_for_ci_run(
    commit,
    *,
    wait_seconds=CI_WAIT_SECONDS,
    poll_seconds=CI_POLL_SECONDS,
    sleep=time.sleep,
    monotonic=time.monotonic,
):
    """Wait for CI on a commit to conclude, because a tag often lands while its run is going."""
    deadline = monotonic() + wait_seconds
    runs = {}
    failures = 0
    while True:
        try:
            runs = read_ci_runs(commit)
        except subprocess.CalledProcessError:
            failures += 1
            if failures >= CI_LOOKUP_ATTEMPTS or monotonic() >= deadline:
                raise
        else:
            failures = 0
            if any(run["conclusion"] == "success" for run in runs.values()):
                return runs
            # Without a run still in progress, only a newly started manual run can change that.
            if runs and all(run["conclusion"] for run in runs.values()):
                return runs
        if monotonic() >= deadline:
            return runs
        sleep(poll_seconds)


def validate_source(tag, *, event_name, tag_created):
    """Require a new stable tag on a tested main commit."""
    if event_name == "push" and tag_created != "true":
        raise ValueError("Release tags must be new; do not move or overwrite an existing tag.")
    if version_number(tag) is None:
        raise ValueError("Release tags must use X.Y.Z without a v prefix or prerelease suffix.")
    commit = read_command("git", "rev-parse", "HEAD")
    tag_commit = read_command("git", "rev-parse", f"refs/tags/{tag}^{{commit}}")
    if commit != tag_commit:
        raise ValueError("The release tag no longer points at the triggering commit.")
    read_command("git", "merge-base", "--is-ancestor", "HEAD", "origin/main")
    # Bot merges can suppress push workflows; manual CI must test the same main commit.
    if any(run["conclusion"] == "success" for run in wait_for_ci_run(commit).values()):
        return commit
    raise ValueError(
        "The release commit must have a successful push or manually dispatched CI run on main. "
        "Run CI on main for this exact commit and wait for it to pass before retrying."
    )


def release_plan(tag, releases):
    """Preserve existing releases and never promote a lower stable version."""
    version = version_number(tag)
    if version is None:
        raise ValueError("Release tags must use X.Y.Z without a v prefix or prerelease suffix.")
    existing = next((release for release in releases if release["tag_name"] == tag), None)
    if existing and existing["prerelease"]:
        raise ValueError("This tag already has a prerelease; do not publish it as a stable image.")
    published_versions = [
        number
        for release in releases
        if not release["draft"] and not release["prerelease"]
        if (number := version_number(release["tag_name"], legacy=True)) is not None
    ]
    latest = not published_versions or version >= max(published_versions)
    if existing and not existing["draft"]:
        return [], latest
    latest_option = f"--latest={str(latest).lower()}"
    if existing:
        return ["edit", tag, "--draft=false", "--verify-tag", latest_option], latest
    return [
        "create",
        tag,
        "--verify-tag",
        "--title",
        tag,
        "--generate-notes",
        latest_option,
    ], latest


def publish_release(repository, tag, *, check_only=False):
    if version_number(tag) is None:
        raise ValueError("Release tags must use X.Y.Z without a v prefix or prerelease suffix.")
    result = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", f"repos/{repository}/releases"],
        check=True,
        capture_output=True,
        text=True,
    )
    releases = [release for page in json.loads(result.stdout) for release in page]
    arguments, latest = release_plan(tag, releases)
    if arguments and not check_only:
        subprocess.run(["gh", "release", *arguments, "--repo", repository], check=True)
    return latest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate without changing a release.")
    arguments = parser.parse_args()
    tag = os.environ["RELEASE_TAG"]
    repository = os.environ["GITHUB_REPOSITORY"]
    if arguments.check:
        validate_source(
            tag,
            event_name=os.environ["EVENT_NAME"],
            tag_created=os.environ.get("TAG_CREATED", ""),
        )
    latest = publish_release(repository, tag, check_only=arguments.check)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        if arguments.check:
            output.write(f"image=ghcr.io/{repository.lower()}\nversion={tag}\n")
        else:
            output.write(f"latest={str(latest).lower()}\n")


if __name__ == "__main__":
    main()
