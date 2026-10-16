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


def test_release_build_is_isolated_from_write_token(workflows):
    release = yaml.safe_load((workflows / "release.yml").read_text())
    # Repository-controlled builds run with explicit read-only permissions.
    build = release["jobs"]["build"]
    assert build["permissions"] == {"contents": "read"}

    # The write token exists only on a fresh runner after the build completes.
    publish = release["jobs"]["publish"]
    assert publish["needs"] == "build"
    assert publish["permissions"] == {"contents": "write"}
    token_steps = [step for step in publish["steps"] if "GH_TOKEN" in step.get("env", {})]
    assert len(token_steps) == 1
    assert token_steps[0]["name"] == "Publish GitHub release"


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
    jobs = release["jobs"]
    job = jobs.get("validate") or jobs.get("build") or jobs["release"]
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

    assert build["permissions"] == {"contents": "read", "checks": "read"}
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
    [name for name, combo in gs.COMBOS.items() if {"js", "ts", "css"} & set(combo["languages"])],
)
def test_npm_release_disables_lifecycle_scripts(tmp_path, name):
    root = gs.render_combo(name, tmp_path / "quiet-otter")
    release = yaml.safe_load((root / ".github/workflows/release.yml").read_text())
    commands = "\n".join(step.get("run", "") for step in release["jobs"]["build"]["steps"])
    assert "npm ci --ignore-scripts" in commands
    assert "npm pack --ignore-scripts --pack-destination dist" in commands


def test_auto_release_isolates_project_code_from_write_token(workflows):
    path = workflows / "auto-release.yml"
    if not path.exists():
        return
    jobs = yaml.safe_load(path.read_text())["jobs"]
    validation = jobs.get("validate") or jobs.get("build")
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
    jobs = yaml.safe_load(path.read_text())["jobs"]
    job = jobs.get("validate") or jobs["build"]
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
    validation_name = "validate" if "validate" in jobs else "build"
    validate = jobs[validation_name]
    build = next(s for s in validate["steps"] if s.get("id") in {"build", "tag"})
    assert 'echo "version=$version" >> "$GITHUB_OUTPUT"' in build["run"]
    assert validate["outputs"]["version"] == ("${{ steps." + build["id"] + ".outputs.version }}")
    release = jobs["release"]
    tag = next(s for s in release["steps"] if s.get("id") == "tag")
    assert tag["env"]["VERSION"] == ("${{ needs." + validation_name + ".outputs.version }}")
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
    assert not any(
        step.get("uses", "").startswith("actions/checkout@") for step in publish["steps"]
    )
    attach = next(s for s in publish["steps"] if s.get("name") == "Attach binaries")
    assert attach["env"]["GH_REPO"] == "${{ github.repository }}"


def test_auto_release_downloads_use_verified_action_contract():
    # Verified against actions/download-artifact v8.0.1 action.yml at this SHA:
    # same-run access needs no github-token; digest-mismatch defaults to error.
    download_action = "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"
    root = Path(__file__).resolve().parents[1] / ".github/workflows"
    release = yaml.safe_load((root / "auto-release.yml").read_text())["jobs"]
    ci = yaml.safe_load((root / "ci.yml").read_text())["jobs"]
    expected = [
        {"name": "python-dist", "path": "dist/", "digest-mismatch": "error"},
        {
            "pattern": "binary-*",
            "path": "binaries/",
            "merge-multiple": True,
            "digest-mismatch": "error",
        },
    ]
    downloads = []
    for job_name in ["release", "publish-binaries"]:
        job = release[job_name]
        assert "GH_TOKEN" not in job.get("env", {})
        steps = [
            step
            for step in job["steps"]
            if step.get("uses", "").startswith("actions/download-artifact@")
        ]
        assert len(steps) == 1
        downloads.extend(steps)
    for step, inputs in zip(downloads, expected, strict=True):
        assert step["uses"] == download_action
        # Exact input sets also forbid cross-run/repository and token overrides.
        assert step["with"] == inputs
        assert "GH_TOKEN" not in step.get("env", {})

    # CI must execute the same immutable action and both input contracts, with
    # read-only permissions and no release command or project checkout.
    smoke = ci["artifact-download-contract"]
    assert smoke["permissions"] == {"contents": "read"}
    assert "artifact-download-contract" in ci["ci"]["needs"]
    assert "continue-on-error" not in smoke
    assert "GH_TOKEN" not in smoke.get("env", {})
    smoke_downloads = [
        step
        for step in smoke["steps"]
        if step.get("uses", "").startswith("actions/download-artifact@")
    ]
    assert len(smoke_downloads) == 2
    for step, inputs in zip(smoke_downloads, expected, strict=True):
        assert step["uses"] == download_action
        assert step["with"] == inputs
        assert "continue-on-error" not in step
        assert "GH_TOKEN" not in step.get("env", {})
    commands = "\n".join(step.get("run", "") for step in smoke["steps"])
    assert "gh release" not in commands
    assert not any(step.get("uses", "").startswith("actions/checkout@") for step in smoke["steps"])
