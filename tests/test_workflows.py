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
    steps = release["jobs"]["validate"]["steps"]
    wait_index, wait = next(
        (i, step)
        for i, step in enumerate(steps)
        if step.get("name") == "Wait for this commit's ci check"
    )
    env = release["jobs"]["validate"].get("env", {}) | wait.get("env", {})
    assert env["SHA"] == "${{ github.sha }}"
    assert "commits/$SHA/check-runs?check_name=ci" in wait["run"]
    assert '.app.slug == "github-actions"' in wait["run"]
    assert 'not releasing it"; exit 1' in wait["run"]
    for i, step in enumerate(steps):
        if step.get("uses", "").startswith("actions/checkout@"):
            assert i > wait_index
            assert step["with"]["ref"] == "${{ github.sha }}"
            assert step["with"]["fetch-depth"] == 0


def test_auto_release_isolates_project_code_from_write_token(workflows):
    path = workflows / "auto-release.yml"
    if not path.exists():
        return
    jobs = yaml.safe_load(path.read_text())["jobs"]
    validation = jobs.get("validate")
    assert validation is not None
    assert validation["permissions"]["contents"] == "read"
    assert any("uv run pytest" in step.get("run", "") for step in validation["steps"])
    assert any("uv build" in step.get("run", "") for step in validation["steps"])
    for step in validation["steps"]:
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False

    privileged = [
        job for job in jobs.values() if job.get("permissions", {}).get("contents") == "write"
    ]
    assert privileged
    for job in privileged:
        commands = "\n".join(step.get("run", "") for step in job["steps"])
        assert "uv sync" not in commands
        assert "pytest" not in commands
        assert "uv build" not in commands


def test_ci_poll_auth_is_scoped_to_read_only_step(workflows):
    path = workflows / "auto-release.yml"
    if not path.exists():
        return
    job = yaml.safe_load(path.read_text())["jobs"]["validate"]
    assert job["permissions"] == {"contents": "read", "checks": "read"}
    assert "GH_TOKEN" not in job.get("env", {})
    wait = next(s for s in job["steps"] if s.get("name") == "Wait for this commit's ci check")
    assert wait["env"]["GH_TOKEN"] == "${{ github.token }}"  # noqa: S105
    assert all("GH_TOKEN" not in s.get("env", {}) for s in job["steps"] if s is not wait)


def test_release_uses_built_version_even_if_latest_tag_changes(workflows, tmp_path):
    import os
    import subprocess

    path = workflows / "auto-release.yml"
    if not path.exists():
        return
    jobs = yaml.safe_load(path.read_text())["jobs"]
    validate = jobs["validate"]
    build = next(s for s in validate["steps"] if s.get("id") == "build")
    assert 'echo "version=$version" >> "$GITHUB_OUTPUT"' in build["run"]
    assert validate["outputs"]["version"] == "${{ steps.build.outputs.version }}"
    release = jobs["release"]
    tag = next(s for s in release["steps"] if s.get("id") == "tag")
    assert tag["env"]["VERSION"] == "${{ needs.validate.outputs.version }}"
    assert "latest=" not in tag["run"]
    # Simulate a newer manual tag appearing after validation. The real writer
    # script must tag the built version, and refuse a duplicate on a rerun.
    repo = tmp_path / "tag-race"
    repo.mkdir()
    for args in (["init"], ["commit", "--allow-empty", "-m", "initial"], ["tag", "v99.0.0"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    env = os.environ | {"VERSION": "1.2.0", "GITHUB_OUTPUT": str(tmp_path / "output")}
    result = subprocess.run(["bash", "-c", tag["run"]], cwd=repo, env=env, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "output").read_text() == "version=1.2.0\n"
    assert (
        subprocess.run(["git", "rev-parse", "v1.2.0"], cwd=repo, capture_output=True).returncode
        == 0
    )
    assert (
        subprocess.run(
            ["bash", "-c", tag["run"]], cwd=repo, env=env, capture_output=True
        ).returncode
        != 0
    )
    for step in release["steps"]:
        if step.get("name") in {"Push tag", "Publish"}:
            assert step["env"]["VERSION"] == "${{ steps.tag.outputs.version }}"


def test_partial_rust_matrix_can_publish_only_after_release_success():
    jobs = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github/workflows/auto-release.yml").read_text()
    )["jobs"]
    assert jobs["binaries"]["strategy"]["fail-fast"] is False
    publish = jobs["publish-binaries"]
    assert publish["needs"] == ["release", "binaries"]
    # A status function suppresses GitHub's implicit success() matrix gate;
    # the explicit release gate still rejects failed/skipped releases.
    assert publish["if"] == "${{ !cancelled() && needs.release.result == 'success' }}"
    download = publish["steps"][0]["with"]
    assert download["pattern"] == "binary-*"
    assert download["merge-multiple"] is True
    assert "continue-on-error" not in publish
