"""Checks for the acceptance harness's artifact and renderer comparisons."""

import json
import runpy
import shutil
import zipfile
from pathlib import Path

import pytest

CHECK = runpy.run_path(str(Path(__file__).parents[1] / "scripts/distribution-check"))


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


def _repository(**overrides):
    data = {
        "id": CHECK["REPOSITORY_ID"],
        "full_name": "Shorewood-Analytics/makeghrepo",
        "name": "makeghrepo",
        "owner": {"login": "Shorewood-Analytics"},
    }
    data.update(overrides)
    return data


def test_repository_is_resolved_from_its_immutable_id_not_a_slug():
    calls = []

    def runner(command):
        calls.append(command)
        return json.dumps(
            _repository(full_name="New-Owner/renamed", name="renamed", owner={"login": "New-Owner"})
        )

    assert CHECK["resolve_repository"](runner) == "New-Owner/renamed"
    assert calls == [
        ["gh", "api", "--hostname", "github.com", f"repositories/{CHECK['REPOSITORY_ID']}"]
    ]
    assert "benpshore" not in json.dumps(calls)


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": CHECK["REPOSITORY_ID"] + 1},  # a repository that reused the slug
        {"id": str(CHECK["REPOSITORY_ID"])},
        {"full_name": "../../evil", "name": "evil", "owner": {"login": "../.."}},
        {
            "full_name": "Shorewood-Analytics/makeghrepo",
            "name": "other",
        },  # owner/name disagree with the slug
        {"owner": None},
        {"full_name": None},
    ],
)
def test_resolution_mismatches_fail_closed(overrides):
    with pytest.raises(RuntimeError):
        CHECK["resolve_repository"](lambda command: json.dumps(_repository(**overrides)))
    with pytest.raises((RuntimeError, ValueError)):
        CHECK["resolve_repository"](lambda command: "[]")


def test_release_download_uses_the_resolved_slug_only(tmp_path):
    command = CHECK["release_download_command"](
        "New-Owner/renamed", "v1.2.3", tmp_path, "makeghrepo-1.2.3-py3-none-any.whl"
    )
    assert command[:6] == ["gh", "release", "download", "v1.2.3", "--repo", "New-Owner/renamed"]
    for bad in ["../x", "owner", "a/b/c", "owner/re po"]:
        with pytest.raises(RuntimeError):
            CHECK["release_download_command"](bad, "v1.2.3", tmp_path, "x")
