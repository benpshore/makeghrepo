"""scripts/trash-test-repo.sh against a fake `gh`: every guard runs before any deletion."""

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import git
import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "trash-test-repo.sh"
NOW = 1_800_000_000
NAME = "mgr-test-s0d-1"

FAKE_GH = """#!/usr/bin/env python3
import json, os, sys

args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
with open(os.environ["FAKE_GH_FIXTURE"]) as f:
    fx = json.load(f)
if args[:2] == ["api", "user"]:
    print(fx["login"])
elif args[0] == "api" and args[1].startswith("repos/"):
    if fx.get("repo") is None:
        print("gh: Not Found (HTTP 404)", file=sys.stderr)
        sys.exit(1)
    print(fx["repo"]["owner"] + "\\t" + fx["repo"]["created_at"])
elif args[:2] == ["project", "list"]:
    title = args[args.index("--jq") + 1].split('select(.title == "')[1].split('")')[0]
    for p in fx.get("projects", []):
        if p["title"] == title:
            print(p["number"])
elif args[:2] in (["project", "delete"], ["repo", "delete"]):
    pass
else:
    print("unexpected gh call: " + " ".join(args), file=sys.stderr)
    sys.exit(99)
"""


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fixture(**overrides):
    fx = {
        "login": "me",
        "repo": {"owner": "me", "created_at": _iso(NOW - 3600)},
        "projects": [
            {"number": 3, "title": NAME},
            {"number": 4, "title": "keep-me"},
            {"number": 9, "title": NAME},
        ],
    }
    fx.update(overrides)
    return fx


def _clone(base: Path, name: str = NAME, origin: str = f"https://github.com/me/{NAME}.git"):
    path = base / name
    path.mkdir(parents=True)
    git.Repo.init(path).create_remote("origin", origin)
    return path


@pytest.fixture
def trash(tmp_path):
    """Run the script with a fake gh; returns (result, gh calls)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "gh"
    fake.write_text(FAKE_GH)
    fake.chmod(0o755)
    base = tmp_path / "code"
    base.mkdir()

    def run(*args, fixture=None):
        log = tmp_path / "gh.log"
        log.write_text("")
        fx = tmp_path / "fixture.json"
        fx.write_text(json.dumps(fixture if fixture is not None else _fixture()))
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "MAKEGHREPO_DIR": str(base),
            "FAKE_GH_LOG": str(log),
            "FAKE_GH_FIXTURE": str(fx),
            "TRASH_NOW_EPOCH": str(NOW),
        }
        result = subprocess.run(
            ["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True
        )
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        return result, calls

    run.base = base
    return run


def _deletes(calls):
    return [c for c in calls if c[:2] in (["project", "delete"], ["repo", "delete"])]


@pytest.mark.parametrize(
    "args",
    [
        ["makeghrepo"],
        ["pi5core"],
        ["mgr-test"],
        ["mgr-test-"],
        ["../mgr-test-x"],
        ["mgr-test-x/y"],
        ["MGR-TEST-X"],
        ["x-trash-y"],
        ["mgr-test-a.b"],
    ],
)
def test_refuses_names_that_are_not_throwaway(trash, args):
    result, calls = trash(*args)
    assert result.returncode == 1
    assert "not a throwaway name" in result.stderr
    assert calls == []


@pytest.mark.parametrize("args", [[], [NAME, "mgr-test-other"], ["--force", NAME], ["-trash"]])
def test_usage_errors(trash, args):
    result, calls = trash(*args)
    assert result.returncode == 2
    assert calls == []


def test_refuses_a_repo_older_than_24_hours(trash):
    old = _fixture(repo={"owner": "me", "created_at": _iso(NOW - 86400)})
    result, calls = trash(NAME, fixture=old)
    assert result.returncode == 1 and "over 24 hours" in result.stderr
    assert _deletes(calls) == []


def test_refuses_a_repo_owned_by_someone_else(trash):
    theirs = _fixture(repo={"owner": "someone-else", "created_at": _iso(NOW - 60)})
    result, calls = trash(NAME, fixture=theirs)
    assert result.returncode == 1 and "belongs to 'someone-else'" in result.stderr
    assert _deletes(calls) == []


def test_refuses_a_missing_repo(trash):
    result, calls = trash(NAME, fixture=_fixture(repo=None))
    assert result.returncode == 1 and "not found" in result.stderr
    assert _deletes(calls) == []


def test_refuses_a_symlinked_local_clone(trash, tmp_path):
    real = _clone(tmp_path / "elsewhere")
    (trash.base / NAME).symlink_to(real)
    result, calls = trash(NAME)
    assert result.returncode == 1 and "symlink" in result.stderr
    assert _deletes(calls) == []
    assert real.exists()


def test_refuses_a_local_clone_of_another_repo(trash):
    clone = _clone(trash.base, origin="https://github.com/me/something-important.git")
    result, calls = trash(NAME)
    assert result.returncode == 1 and "something-important" in result.stderr
    assert _deletes(calls) == []
    assert clone.exists()


def test_deletes_exact_title_boards_then_repo_then_clone(trash):
    clone = _clone(trash.base)
    result, calls = trash(NAME)
    assert result.returncode == 0, result.stderr
    assert _deletes(calls) == [
        ["project", "delete", "3", "--owner", "me"],
        ["project", "delete", "9", "--owner", "me"],
        ["repo", "delete", f"me/{NAME}", "--yes"],
    ]
    assert not clone.exists()
    assert result.stdout.splitlines()[-1] == f"- remove {clone}"


def test_ssh_origin_and_missing_clone_are_fine(trash):
    _clone(trash.base, origin=f"git@github.com:me/{NAME}")
    result, _ = trash(NAME)
    assert result.returncode == 0, result.stderr

    result, calls = trash("gone-trash", fixture=_fixture(projects=[]))
    assert result.returncode == 0, result.stderr
    assert _deletes(calls) == [["repo", "delete", "me/gone-trash", "--yes"]]


def test_dry_run_deletes_nothing(trash):
    clone = _clone(trash.base)
    result, calls = trash("--dry-run", NAME)
    assert result.returncode == 0, result.stderr
    assert _deletes(calls) == []
    assert clone.exists()
    assert all(line.startswith("- would ") for line in result.stdout.splitlines())


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck_is_clean():
    result = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
