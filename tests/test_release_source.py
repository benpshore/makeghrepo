"""A reusable release may build only the source in its caller's trust context."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def build_steps():
    workflow = yaml.safe_load((ROOT / ".github/workflows/release-build.yml").read_text())
    return workflow["jobs"]["python"]["steps"]


def run_guard(directory, source, caller):
    guard = build_steps()[0]
    assert guard["id"] == "validate-source"
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", guard["run"]],
        cwd=directory,
        env=os.environ | {"SOURCE": source, "CALLER_SHA": caller},
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_release_source_is_validated_before_any_checkout_or_local_action():
    steps = build_steps()
    guard, checkout = steps[:2]
    assert guard["id"] == "validate-source"
    assert guard["env"] == {"CALLER_SHA": "${{ github.sha }}"}
    assert guard["shell"] == "bash"
    assert "if" not in guard and "continue-on-error" not in guard
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"] == {
        "ref": "${{ github.sha }}",
        "fetch-depth": 0,
        "persist-credentials": False,
    }
    assert steps[2]["uses"] == "./.github/actions/uv"


@pytest.mark.parametrize(
    "source",
    ["b" * 40, "", "a" * 7, "main", "refs/pull/1/head", "$(touch injected)", "a" * 40 + "\n"],
)
def test_mismatched_or_malformed_source_fails_before_project_code(tmp_path, source):
    result = run_guard(tmp_path, source, "a" * 40)
    assert result.returncode != 0
    assert "release source must equal the caller commit" in result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("source", ["", "main", "A" * 40, "a" * 39])
def test_even_matching_malformed_values_fail_closed(tmp_path, source):
    assert run_guard(tmp_path, source, source).returncode != 0


def test_exact_caller_source_is_accepted(tmp_path):
    result = run_guard(tmp_path, "a" * 40, "a" * 40)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("annotated", [False, True])
def test_older_source_retry_still_uses_its_exact_caller_commit(tmp_path, manual, annotated):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init")
    git("commit", "--allow-empty", "-m", "release source")
    source = git("rev-parse", "HEAD")
    git("tag", *(["-a", "v0.9.0", "-m", "v0.9.0"] if annotated else ["v0.9.0"]))
    git("commit", "--allow-empty", "-m", "newer main")
    git("tag", "v0.10.0")
    git("checkout", "--detach", "v0.9.0")
    assert git("rev-parse", "HEAD") == source
    command = [sys.executable, str(ROOT / "scripts/release-version")]
    if manual:
        command += ["--tag", "v0.9.0"]
    output = subprocess.check_output(command, cwd=tmp_path, text=True)
    plan = dict(line.split("=", 1) for line in output.splitlines())
    assert plan == {"version": "0.9.0", "sha": source}
    result = run_guard(tmp_path, plan["sha"], source)
    assert result.returncode == 0, result.stderr
