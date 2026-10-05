"""Exercise real tag selection, including the previously unverified lookup edge."""

import runpy
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/release-version"
select = runpy.run_path(str(SCRIPT))["select"]


def test_version_selection_is_numeric_stable_and_retryable():
    assert select([], []) == "0.1.0"
    assert select(["v0.9.0", "v0.10.8", "v99.0.0-rc1", "vbroken"], []) == "0.11.0"
    assert select(["v0.9.0", "v1.2.3"], ["v0.9.0"]) == "0.9.0"
    assert select(["v1.2.3", "v01.8.0"], []) == "1.3.0"
    with pytest.raises(ValueError, match="multiple"):
        select(["v0.1.0", "v0.2.0"], ["v0.1.0", "v0.2.0"])


def test_real_git_lookup_uses_source_not_newer_tag(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()
    git("init")
    git("config", "user.name", "Release fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("commit", "--allow-empty", "-m", "source")
    source = git("rev-parse", "HEAD")
    git("tag", "-a", "v0.9.0", "-m", "v0.9.0")
    git("commit", "--allow-empty", "-m", "newer source")
    git("tag", "v0.10.0")
    git("checkout", source)
    output = subprocess.check_output([sys.executable, str(SCRIPT)], cwd=tmp_path, text=True)
    assert output == f"version=0.9.0\nsha={source}\n"
    wrong = subprocess.run([sys.executable, str(SCRIPT), "--tag", "v0.10.0"], cwd=tmp_path, capture_output=True)
    assert wrong.returncode != 0
