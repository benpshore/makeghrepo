import json

import pytest

from makeghrepo import github


class FakeGh(github.Gh):
    """Records gh invocations and returns canned responses instead of running gh."""

    def __init__(self, responses=None, fail_on=()):
        super().__init__(dry_run=False, log=lambda _: None)
        self.calls = []
        self.responses = responses or {}
        self.fail_on = fail_on

    def run(self, *args, body=None, mutate=True):
        self.calls.append((args, body))
        joined = " ".join(args)
        if any(f in joined for f in self.fail_on):
            raise github.GhError(f"boom: {joined}")
        for key, value in self.responses.items():
            if key in joined:
                return value
        return ""


def test_create_repo_uses_source_and_push(tmp_path):
    gh = FakeGh()
    github.create_repo(gh, "me/quiet-otter", tmp_path, "desc", private=False)
    ((args, _),) = gh.calls
    assert args[:3] == ("repo", "create", "me/quiet-otter")
    assert "--public" in args and "--push" in args
    assert args[args.index("--source") + 1] == str(tmp_path)


def test_ruleset_created_when_absent():
    gh = FakeGh(responses={"GET": "[]"})
    github.configure_ruleset(gh, "me/r")
    methods = [a[2] for a, _ in gh.calls]
    assert methods == ["GET", "POST"]
    assert gh.calls[1][1]["name"] == github.RULESET_NAME


def test_ruleset_updated_when_present():
    existing = json.dumps([{"id": 42, "name": github.RULESET_NAME}])
    gh = FakeGh(responses={"GET": existing})
    github.configure_ruleset(gh, "me/r")
    args, _ = gh.calls[1]
    assert args[2] == "PUT" and args[-1] == "repos/me/r/rulesets/42"


def test_ruleset_body_is_solo_friendly():
    rules = {r["type"]: r.get("parameters") for r in github.ruleset_body()["rules"]}
    assert rules["pull_request"]["required_approving_review_count"] == 0
    assert rules["required_status_checks"]["required_status_checks"] == [
        {"context": github.REQUIRED_CHECK}
    ]
    assert {"deletion", "non_fast_forward"} <= rules.keys()


def test_mute_notifications_ignores_repo():
    gh = FakeGh()
    github.mute_notifications(gh, "me/r")
    args, body = gh.calls[0]
    assert args[2:4] == ("PUT", "-H") and args[-1] == "repos/me/r/subscription"
    assert body == {"subscribed": False, "ignored": True}


def test_project_reuses_existing_board():
    listing = json.dumps({"projects": [{"number": 7, "title": "r"}]})
    gh = FakeGh(responses={"project list": listing})
    github.configure_project(gh, "me/r")
    verbs = [a[1] for a, _ in gh.calls]
    assert verbs == ["list", "link"]
    assert gh.calls[1][0][2] == "7"


def test_project_created_when_missing():
    gh = FakeGh(responses={"project list": '{"projects": []}', "project create": '{"number": 3}'})
    github.configure_project(gh, "me/r")
    assert [a[1] for a, _ in gh.calls] == ["list", "create", "link"]


def test_configure_all_continues_after_failure():
    gh = FakeGh(
        responses={
            "GET": "[]",
            "project list": '{"projects": []}',
            "project create": '{"number": 1}',
        },
        fail_on=("secret_scanning", "automated-security-fixes"),
    )
    report = github.configure_all(gh, "me/r")
    assert len(report.failed) == 1  # only the security step (it stops at its first failure)
    assert len(report.ok) == 6


def test_dry_run_never_executes(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("subprocess should not run in dry-run")

    monkeypatch.setattr(github.subprocess, "run", boom)
    lines = []
    gh = github.Gh(dry_run=True, log=lines.append)
    report = github.configure_all(gh, "me/r")
    assert not report.failed
    assert any("rulesets" in line for line in lines)


@pytest.mark.parametrize(
    ("header", "expected"),
    [("X-Oauth-Scopes: repo, workflow, read:org", {"repo", "workflow", "read:org"}), ("", set())],
)
def test_token_scopes(header, expected):
    gh = FakeGh(responses={"-i user": f"HTTP/2.0 200 OK\n{header}\n\n{{}}"})
    assert gh.token_scopes() == expected
