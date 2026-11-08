"""makeghrepo authenticates with the classic PAT gh already has. Nothing in it may run,
suggest or document `gh auth`: its refresh/login subcommands start a new sign-in and
replace the stored credential.
"""

import ast
import re
import subprocess
from pathlib import Path

import pytest

from makeghrepo import github

ROOT = Path(__file__).resolve().parents[1]
GH_AUTH = re.compile(r"\bgh\s+auth\b")

# Verbatim formats from cli/cli: api/client.go generateScopesSuggestion and
# pkg/cmd/project/shared/queries handleError.
API_HINT = (
    'gh: This API operation needs the "project" scope. '
    "To request it, run:  gh auth refresh -h github.com -s project"
)
PROJECT_HINT = (
    "error: your authentication token is missing required scopes [project]\n"
    "To request it, run:  gh auth refresh -s project"
)


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (API_HINT, 'gh: This API operation needs the "project" scope.'),
        (PROJECT_HINT, "error: your authentication token is missing required scopes [project]"),
        ("first line\nplease run gh auth login\nlast line", "first line\nlast line"),
    ],
)
def test_reauthentication_hints_are_removed(stderr, expected):
    assert github.without_auth_hint(stderr) == expected


def test_unrelated_errors_are_unchanged():
    msg = "GraphQL: Could not resolve to a Repository with the name 'x'"
    assert github.without_auth_hint(msg) == msg


def test_gh_failure_never_passes_the_hint_on(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=PROJECT_HINT)

    monkeypatch.setattr(github.subprocess, "run", fake_run)
    with pytest.raises(github.GhError) as err:
        github.gh("project", "list", "--owner", "me")
    assert not GH_AUTH.search(str(err.value))
    assert "missing required scopes [project]" in str(err.value)


def test_source_never_invokes_gh_auth():
    for path in (ROOT / "src/makeghrepo").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "gh":
                first = node.args[0] if node.args else None
                assert not (isinstance(first, ast.Constant) and first.value == "auth"), path
            if isinstance(node, (ast.List, ast.Tuple)):
                words = [e.value for e in node.elts[:2] if isinstance(e, ast.Constant)]
                assert words != ["gh", "auth"], path


def test_nothing_outside_tests_mentions_gh_auth():
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split("\0")
    offenders = []
    for name in tracked:
        path = ROOT / name
        if not name or name.startswith("tests/") or name.endswith(".lock") or not path.is_file():
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        offenders += [
            f"{name}:{number}: {line.strip()}"
            for number, line in enumerate(text.splitlines(), 1)
            if GH_AUTH.search(line)
        ]
    assert offenders == []
