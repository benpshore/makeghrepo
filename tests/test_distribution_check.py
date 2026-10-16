"""Checks for the acceptance harness's artifact and renderer comparisons."""

import hashlib
import json
import runpy
import shutil
import zipfile
from pathlib import Path

import pytest

CHECK = runpy.run_path(str(Path(__file__).parents[1] / "scripts/distribution-check"))


def test_native_checksum_rejects_changed_bytes_and_unexpected_paths(tmp_path):
    binary = tmp_path / "makeghrepo-target"
    binary.write_bytes(b"native fixture")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    checksum = tmp_path / "binary.sha256"
    checksum.write_text(f"{digest}  {binary.name}\n")
    assert CHECK["verify_checksum"](binary, checksum) == digest
    binary.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        CHECK["verify_checksum"](binary, checksum)
    for text in (f"{digest}  ../{binary.name}\n", f"{digest}  {binary.name}\nextra\n"):
        checksum.write_text(text)
        with pytest.raises(RuntimeError, match="invalid native checksum manifest"):
            CHECK["verify_checksum"](binary, checksum)


@pytest.mark.parametrize("change", ["content", "name", "executable", "extra", "symlink", "git"])
def test_render_comparison_detects_real_distribution_differences(tmp_path, change):
    python, rust = tmp_path / "python", tmp_path / "rust"
    python.mkdir()
    (python / ".hidden").write_text("included\n")
    script = python / "hello.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    shutil.copytree(python, rust)
    result = CHECK["compare_trees"](python, rust)
    assert result["files"] == 2 and len(result["tree_sha256"]) == 64
    if change == "content":
        (rust / ".hidden").write_text("different\n")
    elif change == "name":
        (rust / ".hidden").rename(rust / "renamed")
    elif change == "executable":
        (rust / "hello.sh").chmod(0o644)
    elif change == "extra":
        (rust / "extra").touch()
    elif change == "symlink":
        (rust / "outside").symlink_to(script)
    else:
        (rust / ".git").mkdir()
    with pytest.raises(RuntimeError):
        CHECK["compare_trees"](python, rust)


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
