"""The local `uv` action installs exactly the pinned uv and fails closed.

The action is plain shell run by pipx (no third-party action), so its script is executed here the
way the runner executes it: `bash -eo pipefail`, the action's `env`, an isolated pipx home and a
fresh GITHUB_PATH file. Three cases: a clean install, a pre-existing *wrong* uv in pipx (must be
replaced, which is what `--force` guarantees), and an install that fails (nothing may reach PATH).
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = yaml.safe_load((ROOT / ".github/actions/uv/action.yml").read_text())
PINNED = ACTION["inputs"]["version"]["default"]
STEP = ACTION["runs"]["steps"][0]

pipx_available = pytest.mark.skipif(shutil.which("pipx") is None, reason="needs pipx")


def test_action_shape_and_pin():
    # the pin the beta cloud build verifies (beta is an isolated distribution: read, not imported)
    contract = (ROOT / "beta/src/makeghrepo_beta/cloud_contract.py").read_text()
    assert re.search(r'"uv": "([^"]+)"', contract).group(1) == PINNED
    assert ACTION["runs"]["using"] == "composite" and len(ACTION["runs"]["steps"]) == 1
    assert STEP["shell"] == "bash"
    assert "--force" in STEP["run"]
    # the assertion precedes the PATH write, so a failed step leaves PATH untouched
    assert STEP["run"].index("test ") < STEP["run"].index("GITHUB_PATH")


def run_step(tmp_path, version, **extra_env):
    """Run the step's script as the runner would; return (process, GITHUB_PATH text, bin dir)."""
    pipx_home, pipx_bin, github_path = tmp_path / "pipx", tmp_path / "bin", tmp_path / "github_path"
    github_path.write_text("")
    env = {
        **os.environ,
        "UV_VERSION": version,
        "PYTHON_VERSION": "",
        "GITHUB_PATH": str(github_path),
        "PIPX_HOME": str(pipx_home),
        "PIPX_BIN_DIR": str(pipx_bin),
        **extra_env,
    }
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", STEP["run"]],
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )
    return proc, github_path.read_text(), pipx_bin


def uv_version(pipx_bin):
    return subprocess.run(
        [pipx_bin / "uv", "--version"], capture_output=True, text=True, check=True
    ).stdout.split()[1]


@pipx_available
@pytest.mark.slow
def test_clean_install_puts_exactly_the_pinned_uv_on_path(tmp_path):
    proc, github_path, pipx_bin = run_step(tmp_path, PINNED)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert github_path == f"{pipx_bin}\n"
    assert uv_version(pipx_bin) == PINNED
    assert (pipx_bin / "uvx").exists()


@pipx_available
@pytest.mark.slow
def test_pre_existing_wrong_uv_is_replaced_not_kept(tmp_path):
    stale = "0.12.22"
    env = {**os.environ, "PIPX_HOME": str(tmp_path / "pipx"), "PIPX_BIN_DIR": str(tmp_path / "bin")}
    subprocess.run(["pipx", "install", f"uv=={stale}"], check=True, capture_output=True, env=env)
    assert uv_version(tmp_path / "bin") == stale
    proc, github_path, pipx_bin = run_step(tmp_path, PINNED)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert uv_version(pipx_bin) == PINNED
    assert github_path == f"{pipx_bin}\n"
    # Without --force pipx keeps the stale uv ("already seems to be installed"); the version
    # assertion then fails the step and PATH stays untouched. That is the fail-closed floor.
    subprocess.run(
        ["pipx", "install", "--force", f"uv=={stale}"], check=True, capture_output=True, env=env
    )
    without_force = STEP["run"].replace("pipx install --force", "pipx install")
    (tmp_path / "github_path").write_text("")
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", without_force],
        capture_output=True,
        text=True,
        env={
            **env,
            "UV_VERSION": PINNED,
            "PYTHON_VERSION": "",
            "GITHUB_PATH": str(tmp_path / "github_path"),
        },
    )
    assert proc.returncode != 0
    assert (tmp_path / "github_path").read_text() == ""
    assert uv_version(pipx_bin) == stale


# pipx picks its backend: `uv` when a uv is already on PATH (as in CI, where this very action puts
# one there before the tests run), else `pip`. uv ignores PIP_* variables, so the failure is
# injected for both: an unreachable index, no cache, no retries. Offline and fast either way.
UNREACHABLE = "http://127.0.0.1:9/simple"
NO_INDEX = {
    "PIP_INDEX_URL": UNREACHABLE,
    "PIP_RETRIES": "0",
    "UV_DEFAULT_INDEX": UNREACHABLE,
    "UV_INDEX_URL": UNREACHABLE,
    "UV_NO_CACHE": "1",
    "UV_HTTP_RETRIES": "0",
}


@pipx_available
@pytest.mark.parametrize("backend", ["pip", "uv"])
def test_install_failure_stops_the_step_before_anything_reaches_path(tmp_path, backend):
    if backend == "uv" and shutil.which("uv") is None:
        pytest.skip("pipx's uv backend needs a uv on PATH")
    # older pipx has no backend choice and ignores the variable: it installs with pip
    proc, github_path, pipx_bin = run_step(
        tmp_path, PINNED, PIPX_DEFAULT_BACKEND=backend, **NO_INDEX
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert github_path == ""
    assert not (pipx_bin / "uv").exists()
