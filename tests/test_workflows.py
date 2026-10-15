"""Protect the release pipeline in this repo and every generated project."""

from pathlib import Path

import golden_snapshots as gs
import pytest
import yaml


@pytest.fixture(params=["makeghrepo", *sorted(gs.COMBOS)])
def workflows(request, tmp_path):
    root = (
        Path(__file__).resolve().parents[1]
        if request.param == "makeghrepo"
        else gs.render_combo(request.param, tmp_path / "quiet-otter")
    )
    directory = root / ".github/workflows"
    has_release = request.param == "makeghrepo" or (
        "python" in gs.COMBOS[request.param]["languages"]
        and not gs.COMBOS[request.param].get("private", False)
    )
    assert (directory / "auto-release.yml").exists() is has_release
    return directory


def test_ci_preserves_running_and_pending_main_runs(workflows):
    ci = yaml.safe_load((workflows / "ci.yml").read_text())
    # A/B/C main pushes must have distinct groups: disabling cancellation
    # alone still lets C evict pending B. PR updates must keep sharing a group.
    assert ci["concurrency"] == {
        "group": "${{ github.workflow }}-"
        "${{ github.event_name == 'pull_request' && github.ref || github.run_id }}",
        "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
    }
    # A job-level group could reintroduce the same pending-run race.
    assert all("concurrency" not in job for job in ci["jobs"].values())


def test_auto_release_queues_instead_of_replacing_pending_runs(workflows):
    path = workflows / "auto-release.yml"
    if not path.exists():
        return  # Auto-release is only generated for public Python projects.
    release = yaml.safe_load(path.read_text())
    # One shared queue serializes tag allocation; false alone only retains
    # one pending run, so a third merge would silently lose a release.
    assert release["concurrency"] == {
        "group": "auto-release",
        "cancel-in-progress": False,
        "queue": "max",
    }
    assert all("concurrency" not in job for job in release["jobs"].values())


def test_auto_release_keeps_exact_sha_checkout_and_ci_gate(workflows):
    path = workflows / "auto-release.yml"
    if not path.exists():
        return
    release = yaml.safe_load(path.read_text())
    job = release["jobs"].get("build", release["jobs"]["release"])
    steps = job["steps"]
    wait_index, wait = next(
        (i, step)
        for i, step in enumerate(steps)
        if step.get("name") == "Wait for this commit's ci check"
    )
    env = job.get("env", {}) | wait.get("env", {})
    assert env["SHA"] == "${{ github.sha }}"
    assert "commits/$SHA/check-runs?check_name=ci" in wait["run"]
    assert '.app.slug == "github-actions"' in wait["run"]
    assert 'not releasing it"; exit 1' in wait["run"]
    for i, step in enumerate(steps):
        if step.get("uses", "").startswith("actions/checkout@"):
            assert i > wait_index
            assert step["with"]["ref"] == "${{ github.sha }}"
            assert step["with"]["fetch-depth"] == 0


def test_auto_release_isolates_write_credentials_from_project_code(workflows):
    path = workflows / "auto-release.yml"
    if not path.exists():
        return
    release = yaml.safe_load(path.read_text())
    if "build" not in release["jobs"]:
        return  # This regression test covers the generated workflow.
    build = release["jobs"]["build"]
    publish = release["jobs"]["release"]

    assert build["permissions"] == {"contents": "read"}
    assert "GH_TOKEN" not in build.get("env", {})
    checkout = next(
        step for step in build["steps"] if step.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"]["persist-credentials"] is False
    assert publish["permissions"] == {"contents": "write", "actions": "read"}
    download = next(
        step for step in publish["steps"] if step.get("name") == "Download release artifacts"
    )
    assert download["env"]["ARTIFACT_ID"] == "${{ needs.build.outputs.artifact-id }}"
    assert "actions/artifacts/$ARTIFACT_ID/zip" in download["run"]
    assert not any(
        command in step.get("run", "")
        for step in publish["steps"]
        for command in ("uv sync", "uv run pytest", "uv build")
    )


def test_tag_release_isolates_build_code_from_write_token(workflows):
    release = yaml.safe_load((workflows / "release.yml").read_text())
    if "build" not in release["jobs"]:
        return  # This assertion covers generated-project release workflows.
    build = release["jobs"]["build"]
    publish = release["jobs"]["publish"]

    assert build["permissions"] == {"contents": "read"}
    assert publish["permissions"] == {"contents": "write"}
    assert publish["needs"] == "build"

    token_steps = [step for step in publish["steps"] if "GH_TOKEN" in step.get("env", {})]
    assert len(token_steps) == 1
    assert token_steps[0]["name"] == "Publish GitHub release"
    assert token_steps[0]["env"]["GH_TOKEN"] == "${{ github.token }}"  # noqa: S105
    assert token_steps[0]["env"]["GH_REPO"] == "${{ github.repository }}"
    assert not any(
        step.get("uses", "").startswith("actions/checkout@") for step in publish["steps"]
    )
    assert all("GH_TOKEN" not in step.get("env", {}) for step in build["steps"])


@pytest.mark.parametrize(
    "name",
    [
        name
        for name, combo in gs.COMBOS.items()
        if {"js", "ts", "css"} & set(combo["languages"])
    ],
)
def test_npm_release_disables_lifecycle_scripts(tmp_path, name):
    root = gs.render_combo(name, tmp_path / "quiet-otter")
    release = yaml.safe_load((root / ".github/workflows/release.yml").read_text())
    commands = "\n".join(
        step.get("run", "") for step in release["jobs"]["build"]["steps"]
    )
    assert "npm ci --ignore-scripts" in commands
    assert "npm pack --ignore-scripts --pack-destination dist" in commands
