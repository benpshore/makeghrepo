"""Tests for scripts/trash-test-repo.sh, run against a fake `gh` that records its argv.

The script never reaches the real gh: a stub is first on PATH, and the env is built
from scratch (no GH_*/GITHUB_* vars, HOME/GH_CONFIG_DIR/MAKEGHREPO_DIR in tmp_path).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "trash-test-repo.sh"
OWNER = "benpshore"
NAME = "mgr-test-x-1"

FAKE_GH = """\
#!{python}
import json, os, sys

args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
cfg = json.load(open(os.environ["FAKE_GH_CONFIG"]))
cmd = " ".join(args[:2])
if cmd in cfg["fail"]:
    sys.exit(f"fake gh: {{cmd}} failed")
if cmd == "repo view":
    if cfg["created_at"] is None:
        sys.exit("GraphQL: Could not resolve to a Repository")
    print(json.dumps({{"createdAt": cfg["created_at"]}}))
elif cmd == "project list":
    print(json.dumps(cfg["projects"]))
elif cmd == "project delete":
    print(json.dumps({{"number": int(args[2])}}))
elif cmd != "repo delete":
    sys.exit(f"fake gh: unexpected call {{args}}")
"""


def iso_ago(**delta) -> str:
    return (datetime.now(UTC) - timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def boards(*titles_by_number: tuple[int, str], total: int | None = None) -> dict:
    projects = [{"number": n, "title": t} for n, t in titles_by_number]
    return {"projects": projects, "totalCount": len(projects) if total is None else total}


@dataclass
class Result:
    code: int
    out: str
    err: str
    calls: list[list[str]]


class FakeGh:
    def __init__(self, tmp_path: Path):
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH.format(python=sys.executable))
        gh.chmod(0o755)
        self.log = tmp_path / "gh-calls.jsonl"
        self.config = tmp_path / "gh-config.json"
        self.base = tmp_path / "code"  # MAKEGHREPO_DIR
        self.base.mkdir()
        path = f"{self.bin}{os.pathsep}{os.environ['PATH']}"
        assert shutil.which("gh", path=path) == str(gh)
        self.env = {
            "PATH": path,
            "HOME": str(tmp_path / "home"),
            "GH_CONFIG_DIR": str(tmp_path / "gh-config-dir"),
            "MAKEGHREPO_DIR": str(self.base),
            "FAKE_GH_LOG": str(self.log),
            "FAKE_GH_CONFIG": str(self.config),
        }
        self.created_at: str | None = iso_ago(hours=1)
        self.projects = boards()
        self.fail: list[str] = []

    def run(self, *args: str) -> Result:
        self.config.write_text(
            json.dumps(
                {"created_at": self.created_at, "projects": self.projects, "fail": self.fail}
            )
        )
        self.log.write_text("")
        proc = subprocess.run(
            [str(SCRIPT), *args], capture_output=True, text=True, env=self.env, check=False
        )
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        return Result(proc.returncode, proc.stdout, proc.stderr, calls)


@pytest.fixture
def gh(tmp_path) -> FakeGh:
    return FakeGh(tmp_path)


VIEW = ["repo", "view", f"{OWNER}/{NAME}", "--json", "createdAt"]
LIST = ["project", "list", "--owner", OWNER, "--closed", "--limit", "1000", "--format", "json"]
REPO_DELETE = ["repo", "delete", f"{OWNER}/{NAME}", "--yes"]


def board_delete(number: int) -> list[str]:
    return ["project", "delete", str(number), "--owner", OWNER]


# --- file-level sanity ---------------------------------------------------------------


def test_script_is_executable_bash():
    assert SCRIPT.stat().st_mode & 0o111
    assert SCRIPT.read_text().startswith("#!/usr/bin/env bash\n")
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


# --- name guard ------------------------------------------------------------------------


def test_no_args_shows_usage(gh):
    r = gh.run()
    assert r.code != 0
    assert "Usage:" in r.err
    assert r.calls == []


@pytest.mark.parametrize(
    "name",
    [
        "makeghrepo",
        "test-repo",
        "mgr-test",
        "mgr-test-",
        "-trash",
        "trash",
        "my-mgr-test-repo",
        "production-trash-can",
        "mgr-test-../../etc",
        "../x-trash",
        "mgr-test-a/b",
        "mgr-test-a b",
        "mgr-test-$(id)",
        "mgr-test-x\n",
    ],
)
def test_rejects_bad_names_before_any_gh_call(gh, name):
    r = gh.run(name)
    assert r.code != 0
    assert "match '^mgr-test-' or end with '-trash'" in r.err
    assert r.calls == []


@pytest.mark.parametrize(
    "name", ["mgr-test-foo", "mgr-test-s0d-1", "some-repo-trash", "a.b_c-trash"]
)
def test_accepts_good_names(gh, name):
    gh.created_at = None  # stop right after the name guard
    r = gh.run(name)
    assert "match '^mgr-test-'" not in r.err
    assert r.calls == [["repo", "view", f"{OWNER}/{name}", "--json", "createdAt"]]


# --- happy path --------------------------------------------------------------------------


def test_deletes_exact_board_then_local_dir_then_repo(gh):
    gh.projects = boards((7, NAME), (8, f"{NAME}0"), (9, f"x-{NAME}"), (3, "litfeed"))
    local = gh.base / NAME
    (local / ".git").mkdir(parents=True)
    other = gh.base / f"{NAME}0"
    other.mkdir()

    r = gh.run(NAME)

    assert r.code == 0, r.err
    assert r.calls == [VIEW, LIST, board_delete(7), REPO_DELETE]
    assert not local.exists()
    assert other.exists()
    assert "Successfully deleted" in r.out


def test_deletes_every_board_with_the_exact_title(gh):
    gh.projects = boards((7, NAME), (12, NAME))
    r = gh.run(NAME)
    assert r.code == 0, r.err
    assert r.calls == [VIEW, LIST, board_delete(7), board_delete(12), REPO_DELETE]


def test_no_board_and_no_local_dir_still_deletes_repo(gh):
    gh.projects = boards((8, f"{NAME}0"))
    r = gh.run(NAME)
    assert r.code == 0, r.err
    assert r.calls == [VIEW, LIST, REPO_DELETE]
    assert "No project board found" in r.out


def test_local_dir_respects_tilde_in_makeghrepo_dir(gh):
    home = Path(gh.env["HOME"])
    local = home / "elsewhere" / NAME
    local.mkdir(parents=True)
    gh.env["MAKEGHREPO_DIR"] = "~/elsewhere"
    r = gh.run(NAME)
    assert r.code == 0, r.err
    assert not local.exists()


def test_local_dir_defaults_to_home_code_github(gh):
    del gh.env["MAKEGHREPO_DIR"]
    local = Path(gh.env["HOME"]) / "code" / "GitHub" / NAME
    local.mkdir(parents=True)
    r = gh.run(NAME)
    assert r.code == 0, r.err
    assert not local.exists()


# --- age guard -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "delta", [{"hours": 24, "minutes": 50}, {"hours": 24, "seconds": 30}, {"days": 400}]
)
def test_refuses_repos_older_than_24h(gh, delta):
    gh.created_at = iso_ago(**delta)
    gh.projects = boards((7, NAME))
    local = gh.base / NAME
    local.mkdir()
    r = gh.run(NAME)
    assert r.code != 0
    assert "safety limit" in r.err
    assert r.calls == [VIEW]
    assert local.exists()


def test_accepts_repo_just_under_24h(gh):
    gh.created_at = iso_ago(hours=23, minutes=55)
    r = gh.run(NAME)
    assert r.code == 0, r.err
    assert r.calls == [VIEW, LIST, REPO_DELETE]


# --- failures stop before the repo (the age anchor) is deleted ------------------------------


def test_missing_repo_deletes_nothing(gh):
    gh.created_at = None
    gh.projects = boards((7, NAME))
    local = gh.base / NAME
    local.mkdir()
    r = gh.run(NAME)
    assert r.code != 0
    assert "not found" in r.err
    assert r.calls == [VIEW]
    assert local.exists()


@pytest.mark.parametrize(
    ("setup", "expected_calls"),
    [
        ({"fail": ["project list"]}, [VIEW, LIST]),
        ({"projects": {"unexpected": "shape"}}, [VIEW, LIST]),
        ({"projects": boards((7, NAME), total=1001)}, [VIEW, LIST]),
        (
            {"projects": boards((7, NAME)), "fail": ["project delete"]},
            [VIEW, LIST, board_delete(7)],
        ),
    ],
    ids=["list-fails", "list-unparseable", "list-truncated", "board-delete-fails"],
)
def test_board_errors_abort_before_local_dir_and_repo(gh, setup, expected_calls):
    for key, value in setup.items():
        setattr(gh, key, value)
    local = gh.base / NAME
    local.mkdir()
    r = gh.run(NAME)
    assert r.code != 0
    assert r.calls == expected_calls
    assert local.exists()
    assert "Successfully deleted" not in r.out


def test_repo_delete_failure_exits_nonzero(gh):
    gh.fail = ["repo delete"]
    r = gh.run(NAME)
    assert r.code != 0
    assert r.calls == [VIEW, LIST, REPO_DELETE]
    assert "failed to delete repository" in r.err
    assert "Successfully deleted" not in r.out
