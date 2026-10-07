"""Inert regressions for uv controls that bypass the check environment policy."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from makeghrepo import scaffold


def test_check_env_filters_uv_controls_without_changing_parent(monkeypatch):
    parent = {
        "GH_TOKEN": "synthetic-parent-token",
        "GITHUB_TOKEN": "synthetic-parent-token",
        "UV_GITHUB_TOKEN": "synthetic-parent-token",
        "UV_ENV_FILE": "/synthetic/credentials.env",
        "UV_NO_ENV_FILE": "0",
        "UV_PROJECT": "/synthetic/other-project",
        "UV_NO_PROJECT": "1",
        "UV_ISOLATED": "1",
        "UV_WORKING_DIR": "/synthetic/other-directory",
        "UV_PROJECT_ENVIRONMENT": "/synthetic/other-environment",
        "KEEP_ME": "1",
    }
    before = parent.copy()
    monkeypatch.setattr(os, "environ", parent)
    assert scaffold.check_env() == {"UV_NO_ENV_FILE": "1", "KEEP_ME": "1"}
    assert parent == before


@pytest.mark.parametrize("skip", [False, True])
def test_every_check_stage_receives_uv_policy(tmp_path, monkeypatch, skip):
    """Cover probes, required locks, install, and pooled checks at the real caller."""
    parent = {
        "UV_ENV_FILE": "/synthetic/credentials.env",
        "UV_PROJECT_ENVIRONMENT": "/synthetic/other-environment",
        "UV_NO_PROJECT": "1",
        "UV_ISOLATED": "1",
    }
    if skip:
        parent["MAKEGHREPO_SKIP_LOCAL_CHECKS"] = "1"
    monkeypatch.setattr(os, "environ", parent)
    monkeypatch.setattr(scaffold.shutil, "which", lambda _: "/synthetic/tool")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs["env"]))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(scaffold.subprocess, "run", fake_run)
    scaffold.smoke_test(tmp_path, ["python", "docker"], lambda _: None)
    commands = [cmd for cmd, _ in calls]
    assert ("uv", "lock") in commands
    if skip:
        assert commands == [("uv", "lock")]
    else:
        assert ("docker", "info") in commands
        assert ("uv", "sync", "--locked") in commands
        assert any(cmd[:2] == ("uv", "run") for cmd in commands)
    for _, env in calls:
        assert "UV_ENV_FILE" not in env
        assert "UV_PROJECT_ENVIRONMENT" not in env
        assert "UV_NO_PROJECT" not in env
        assert "UV_ISOLATED" not in env
        assert env["UV_NO_ENV_FILE"] == "1"


@pytest.fixture
def uv_executable():
    executable = shutil.which("uv")
    assert executable is not None, "uv is required for these offline regression tests"
    return executable


def inert_uv_env(tmp_path):
    # No inherited environment, user config, network, dependencies, or downloads.
    return {
        "UV_CACHE_DIR": str(tmp_path / "cache"),
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_NO_CONFIG": "1",
    }


def test_real_uv_cannot_reload_synthetic_credentials(tmp_path, monkeypatch, uv_executable):
    dotenv = tmp_path / "synthetic.env"
    dotenv.write_text("GH_TOKEN=synthetic-dotenv-token\n")
    parent = inert_uv_env(tmp_path) | {"UV_ENV_FILE": str(dotenv), "UV_NO_ENV_FILE": "0"}
    monkeypatch.setattr(os, "environ", parent)
    command = [
        uv_executable, "run", "--offline", "--no-project", "--no-sync",
        "--python", sys.executable, sys.executable, "-c",
        'import os, sys; sys.exit(0 if "GH_TOKEN" in os.environ else 7)',
    ]  # fmt: skip
    # Positive control proves the real uv child reads the inert dotenv fixture.
    before = subprocess.run(command, cwd=tmp_path, env=parent, capture_output=True)
    assert before.returncode == 0, before.stderr
    after = subprocess.run(command, cwd=tmp_path, env=scaffold.check_env(), capture_output=True)
    assert after.returncode == 7, after.stderr


def write_inert_project(path: Path):
    path.mkdir()
    (path / "pyproject.toml").write_text(
        '[project]\nname="inert-project"\nversion="0.1.0"\n'
        'requires-python=">=3.12"\ndependencies=[]\n'
    )


@pytest.mark.parametrize("control", ["UV_NO_PROJECT", "UV_ISOLATED"])
def test_real_uv_run_keeps_project_with_unrelated_active_env(
    tmp_path, monkeypatch, uv_executable, control
):
    """Inherited run controls must not bypass the environment made by sync."""
    project = tmp_path / "daughter"
    write_inert_project(project)
    active = tmp_path / "unrelated-active"
    base = inert_uv_env(tmp_path) | {"PATH": str(Path(sys.executable).parent)}
    subprocess.run(
        [uv_executable, "venv", "--offline", "--python", sys.executable, str(active)],
        cwd=tmp_path, env=base, capture_output=True, check=True,
    )  # fmt: skip
    parent = base | {control: "1", "VIRTUAL_ENV": str(active)}
    before_parent = parent.copy()
    monkeypatch.setattr(os, "environ", parent)
    # Both controls apply to run: sync still creates the daughter environment.
    subprocess.run(
        [uv_executable, "sync", "--offline", "--python", sys.executable],
        cwd=project, env=parent, capture_output=True, check=True,
    )  # fmt: skip
    assert (project / ".venv" / "pyvenv.cfg").is_file()
    command = [
        uv_executable, "run", "--offline", "--no-sync", "python", "-c",
        "import sys; print(sys.prefix)",
    ]  # fmt: skip
    # Positive control: the inherited override bypasses the synced project venv.
    before = subprocess.run(command, cwd=project, env=parent, capture_output=True, text=True)
    assert before.returncode == 0, before.stderr
    before_prefix = Path(before.stdout.strip()).resolve()
    if control == "UV_NO_PROJECT":
        assert before_prefix == active.resolve()
    else:
        assert before_prefix.is_relative_to((tmp_path / "cache" / "builds-v0").resolve())
    assert before_prefix != (project / ".venv").resolve()
    after = subprocess.run(
        command, cwd=project, env=scaffold.check_env(), capture_output=True, text=True
    )
    assert after.returncode == 0, after.stderr
    assert Path(after.stdout.strip()).resolve() == (project / ".venv").resolve()
    assert parent == before_parent


@pytest.mark.parametrize("control", ["UV_PROJECT_ENVIRONMENT", "UV_PROJECT", "UV_WORKING_DIR"])
def test_real_uv_sync_stays_in_daughter(tmp_path, monkeypatch, uv_executable, control):
    """Only empty disposable projects are synced; no build backend is defined."""
    other = tmp_path / "other"
    if control != "UV_PROJECT_ENVIRONMENT":
        write_inert_project(other)
    redirected_env = other if control == "UV_PROJECT_ENVIRONMENT" else other / ".venv"
    parent = inert_uv_env(tmp_path) | {control: str(other)}
    monkeypatch.setattr(os, "environ", parent)
    command = [uv_executable, "sync", "--offline", "--python", sys.executable]
    before_project = tmp_path / "before"
    write_inert_project(before_project)
    before = subprocess.run(command, cwd=before_project, env=parent, capture_output=True)
    assert before.returncode == 0, before.stderr
    assert (redirected_env / "pyvenv.cfg").is_file()
    assert not (before_project / ".venv").exists()
    # The corrected route must not retouch the unrelated environment.
    before_metadata = (redirected_env / "pyvenv.cfg").read_bytes()
    after_project = tmp_path / "daughter"
    write_inert_project(after_project)
    after = subprocess.run(
        command, cwd=after_project, env=scaffold.check_env(), capture_output=True
    )
    assert after.returncode == 0, after.stderr
    assert (after_project / ".venv" / "pyvenv.cfg").is_file()
    assert (after_project / "uv.lock").is_file()
    assert (redirected_env / "pyvenv.cfg").read_bytes() == before_metadata
