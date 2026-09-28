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
    return root / ".github/workflows"


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
    steps = release["jobs"]["release"]["steps"]
    wait_index, wait = next(
        (i, step)
        for i, step in enumerate(steps)
        if step.get("name") == "Wait for this commit's ci check"
    )
    env = release["jobs"]["release"].get("env", {}) | wait.get("env", {})
    assert env["SHA"] == "${{ github.sha }}"
    assert "commits/$SHA/check-runs?check_name=ci" in wait["run"]
    assert '.app.slug == "github-actions"' in wait["run"]
    assert 'not releasing it"; exit 1' in wait["run"]
    for i, step in enumerate(steps):
        if step.get("uses", "").startswith("actions/checkout@"):
            assert i > wait_index
            assert step["with"]["ref"] == "${{ github.sha }}"
            assert step["with"]["fetch-depth"] == 0
