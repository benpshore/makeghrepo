import json

import pytest

from makeghrepo import github


class Calls(list):
    responses: dict
    fail_on: tuple


@pytest.fixture
def calls(monkeypatch):
    """Replace gh() with a recorder. Set calls.responses[substr] / calls.fail_on."""
    recorded = Calls()
    recorded.responses = {}
    recorded.fail_on = ()

    def fake_gh(*args, body=None):
        recorded.append((args, body))
        joined = " ".join(args)
        if any(f in joined for f in recorded.fail_on):
            raise github.GhError(f"boom: {joined}")
        return next((v for k, v in recorded.responses.items() if k in joined), "")

    monkeypatch.setattr(github, "gh", fake_gh)
    return recorded


def test_create_repo_adds_remote_without_pushing(calls, tmp_path):
    github.create_repo("me/r", tmp_path, "desc", private=False)
    ((args, _),) = calls
    assert args[:4] == ("repo", "create", "me/r", "--public")
    assert "--push" not in args
    assert args[args.index("--source") + 1] == str(tmp_path)


def test_ruleset_created_when_absent(calls):
    calls.responses["GET"] = "[]"
    github.configure_ruleset("me/r")
    assert [a[2] for a, _ in calls] == ["GET", "POST"]
    assert calls[1][1]["name"] == github.RULESET_NAME


def test_ruleset_updated_when_present(calls):
    calls.responses["GET"] = json.dumps([{"id": 42, "name": github.RULESET_NAME}])
    github.configure_ruleset("me/r")
    assert calls[1][0][2:] == ("PUT", "repos/me/r/rulesets/42")


def test_ruleset_is_solo_friendly_and_tied_to_actions():
    rules = {r["type"]: r.get("parameters") for r in github.ruleset_body()["rules"]}
    assert rules["pull_request"]["required_approving_review_count"] == 0
    assert rules["required_status_checks"]["required_status_checks"] == [
        {"context": github.REQUIRED_CHECK, "integration_id": github.GITHUB_ACTIONS_APP_ID}
    ]
    assert {"deletion", "non_fast_forward"} <= rules.keys()


def test_project_reuses_existing_board(calls):
    calls.responses["project list"] = json.dumps({"projects": [{"number": 7, "title": "r"}]})
    github.configure_project("me/r")
    assert [a[1] for a, _ in calls] == ["list", "link"]
    assert calls[1][0][2] == "7"
    assert "--closed" in calls[0][0]


def test_project_created_when_missing(calls):
    calls.responses["project list"] = '{"projects": []}'
    calls.responses["project create"] = '{"number": 3}'
    github.configure_project("me/r")
    assert [a[1] for a, _ in calls] == ["list", "create", "link"]


def _paths(calls):
    return [a[3] if a[0] == "api" else " ".join(a[:2]) for a, _ in calls]


def test_public_order_security_then_push_then_ruleset(calls):
    calls.responses.update(
        {"GET": "[]", "project list": '{"projects": [{"number": 1, "title": "r"}]}'}
    )
    order = []
    failed = github.configure_all(
        "me/r", private=False, push=lambda: order.append(len(calls)), log=lambda _: None
    )
    assert failed == []
    paths = _paths(calls)
    pushed_at = order[0]
    assert "repos/me/r/private-vulnerability-reporting" in paths[:pushed_at]
    assert "repos/me/r/rulesets?includes_parents=false" in paths[pushed_at:]
    patch = next(b for a, b in calls if a[2] == "PATCH")
    assert "secret_scanning_push_protection" in patch["security_and_analysis"]
    mute = next(b for a, b in calls if a[3].endswith("/subscription"))
    assert mute == {"subscribed": False, "ignored": True}


def test_private_skips_paid_features(calls):
    github.configure_all("me/r", private=True, project=False, log=lambda _: None)
    paths = " ".join(_paths(calls))
    assert "rulesets" not in paths
    assert "private-vulnerability-reporting" not in paths
    patch = next(b for a, b in calls if a[2] == "PATCH")
    assert "security_and_analysis" not in patch


def test_failures_are_collected_and_others_still_run(calls):
    calls.responses["GET"] = "[]"
    calls.fail_on = ("automated-security-fixes",)
    failed = github.configure_all("me/r", private=False, project=False, log=lambda _: None)
    assert failed == ["dependabot security fixes"]
    assert any("subscription" in p for p in _paths(calls))


def test_push_failure_stops_before_ruleset(calls):
    def bad_push():
        raise RuntimeError("rejected")

    failed = github.configure_all("me/r", private=False, push=bad_push, log=lambda _: None)
    assert failed == ["push main"]
    assert not any("rulesets" in p for p in _paths(calls))


def test_repo_exists_distinguishes_missing_from_errors(monkeypatch):
    def missing(*a, **k):
        raise github.GhError("GraphQL: Could not resolve to a Repository with the name 'x'")

    monkeypatch.setattr(github, "gh", missing)
    assert github.repo_exists("me/x") is False

    def offline(*a, **k):
        raise github.GhError("error connecting to api.github.com")

    monkeypatch.setattr(github, "gh", offline)
    with pytest.raises(github.GhError):
        github.repo_exists("me/x")
