"""Checks for the acceptance harness's artifact and renderer comparisons."""

import json
import runpy
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

CHECK = runpy.run_path(str(Path(__file__).parents[1] / "scripts/distribution-check"))


@pytest.mark.parametrize("option", ["-O", "-OO"])
def test_bootstrap_harness_refuses_stripped_acceptance_checks(option):
    harness = Path(__file__).parents[1] / "scripts/bootstrap-check"
    result = subprocess.run(
        [sys.executable, option, str(harness), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "requires assertions enabled" in result.stderr
    assert "scenarios passed" not in result.stdout


@pytest.mark.parametrize("change", ["content", "name", "executable", "extra", "symlink", "git"])
def test_render_comparison_detects_real_distribution_differences(tmp_path, change):
    python, other = tmp_path / "python", tmp_path / "other"
    python.mkdir()
    (python / ".hidden").write_text("included\n")
    script = python / "hello.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    shutil.copytree(python, other)
    result = CHECK["compare_trees"](python, other)
    assert result["files"] == 2 and len(result["tree_sha256"]) == 64
    if change == "content":
        (other / ".hidden").write_text("different\n")
    elif change == "name":
        (other / ".hidden").rename(other / "renamed")
    elif change == "executable":
        (other / "hello.sh").chmod(0o644)
    elif change == "extra":
        (other / "extra").touch()
    elif change == "symlink":
        (other / "outside").symlink_to(script)
    else:
        (other / ".git").mkdir()
    with pytest.raises(RuntimeError):
        CHECK["compare_trees"](python, other)


def test_wheel_metadata_identifies_actual_distribution(tmp_path):
    wheel = tmp_path / "fixture.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "makeghrepo-0.34.0.dist-info/METADATA", "Name: makeghrepo\nVersion: 0.34.0\n"
        )
    assert CHECK["wheel_version"](wheel) == "0.34.0"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("other-0.34.0.dist-info/METADATA", "Name: other\nVersion: 0.34.0\n")
    with pytest.raises(RuntimeError, match="unexpected wheel project"):
        CHECK["wheel_version"](wheel)


def test_installed_receipt_exposes_actual_git_commit(tmp_path):
    directory = (
        tmp_path / "tools/makeghrepo/lib/python3.14/site-packages/makeghrepo-0.34.0.dist-info"
    )
    directory.mkdir(parents=True)
    (directory / "METADATA").write_text("Name: makeghrepo\nVersion: 0.34.0\n")
    (directory / "direct_url.json").write_text(
        json.dumps({"vcs_info": {"vcs": "git", "commit_id": "a" * 40}})
    )
    metadata = CHECK["installed_metadata"]({"UV_TOOL_DIR": str(tmp_path / "tools")})
    assert metadata == {"version": "0.34.0", "commit": "a" * 40}


def test_installed_checks_do_not_inherit_git_auth_or_user_tool_paths(tmp_path, monkeypatch):
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "GIT_DIR", "GIT_SSH_COMMAND", "PYTHONPATH"):
        monkeypatch.setenv(name, "synthetic-fixture")
    monkeypatch.setenv("UV_TOOL_DIR", "/synthetic/user-tools")
    env = CHECK["isolated_env"](tmp_path)
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "GIT_DIR", "GIT_SSH_COMMAND", "PYTHONPATH"):
        assert name not in env
    env = CHECK["tool_env"](tmp_path / "clean-install", env)
    assert env["UV_TOOL_DIR"] == str(tmp_path / "clean-install/tools")
    assert env["UV_TOOL_BIN_DIR"] == str(tmp_path / "clean-install/bin")


def test_distribution_is_required_and_cannot_cancel_its_caller():
    import yaml

    root = Path(__file__).resolve().parents[1]
    ci = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    distribution = yaml.safe_load((root / ".github/workflows/distribution.yml").read_text())
    assert ci["jobs"]["distribution"]["uses"] == "./.github/workflows/distribution.yml"
    assert "distribution" in ci["jobs"]["ci"]["needs"]
    # Reusable workflows inherit github.workflow from the caller. A distinct
    # literal prefix prevents PR cancellation from cancelling the parent run.
    assert distribution["concurrency"]["group"].startswith("distribution-")
    assert distribution["concurrency"]["group"] != ci["concurrency"]["group"]


@pytest.mark.parametrize("candidate", [False, True])
def test_bootstrap_guarantees_are_not_imposed_on_older_published_releases(
    tmp_path,
    monkeypatch,
    candidate,
):
    check = CHECK["check_installed_tools"]
    tools = {"python-wheel": tmp_path / "python", "rust": tmp_path / "rust"}
    commands = []

    def fake_run(command, **kwargs):
        command = [str(part) for part in command]
        if command[-1] == "--version":
            return "makeghrepo 0.35.0"
        if command[-1] in {"--help", "-h"}:
            return "--render --license --version"
        assert command[1].endswith("scripts/bootstrap-check")
        commands.append(command)
        report = Path(command[command.index("--output") + 1])
        report.write_text(json.dumps({str(path.resolve()): [] for path in tools.values()}))
        return ""

    monkeypatch.setitem(check.__globals__, "CASES", ())
    monkeypatch.setitem(check.__globals__, "run", fake_run)
    check(tools, "0.35.0", tmp_path, {"PATH": ""}, python_gate=candidate)
    assert len(commands) == 1
    assert ("--python-executable" in commands[0]) is candidate
    if candidate:
        assert commands[0][commands[0].index("--python-executable") + 1] == str(
            tools["python-wheel"]
        )
