import json
import threading
import time

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


def _graphql_projects(*nodes: dict) -> str:
    return json.dumps({"data": {"user": {"projectsV2": {"nodes": list(nodes)}}}})


def test_project_reuses_existing_board(calls):
    calls.responses["graphql"] = _graphql_projects({"number": 7, "title": "r"})
    github.configure_project("me/r")
    assert [a[1] for a, _ in calls] == ["graphql", "link"]
    assert calls[1][0][2] == "7"


def test_project_reuses_existing_closed_board(calls):
    """The query() filter returns closed projects too — no separate flag needed."""
    calls.responses["graphql"] = _graphql_projects({"number": 9, "title": "r", "closed": True})
    github.configure_project("me/r")
    assert [a[1] for a, _ in calls] == ["graphql", "link"]
    assert calls[1][0][2] == "9"


def test_project_exact_title_match_among_text_search_hits(calls):
    """query() is a substring search: a same-prefix project must not be mistaken for it."""
    calls.responses["graphql"] = _graphql_projects(
        {"number": 4, "title": "r-old"}, {"number": 7, "title": "r"}
    )
    github.configure_project("me/r")
    assert calls[1][0][2] == "7"


def test_project_falls_back_to_full_scan_when_search_page_is_full(calls):
    """A full page of 100 non-matches doesn't prove the board doesn't exist — it could
    be on a second page. Fall back to the old full scan rather than duplicate it."""
    calls.responses["graphql"] = _graphql_projects(
        *({"number": i, "title": f"other-{i}"} for i in range(100))
    )
    calls.responses["project list"] = json.dumps({"projects": [{"number": 42, "title": "r"}]})
    github.configure_project("me/r")
    assert [a[1] for a, _ in calls] == ["graphql", "list", "link"]
    assert calls[2][0][2] == "42"


def test_project_created_when_missing(calls):
    calls.responses["graphql"] = _graphql_projects()
    calls.responses["project create"] = '{"number": 3}'
    github.configure_project("me/r")
    assert [a[1] for a, _ in calls] == ["graphql", "create", "link"]


def _paths(calls):
    return [a[3] if a[0] == "api" else " ".join(a[:2]) for a, _ in calls]


def test_public_settles_settings_before_fanout_before_ruleset(calls):
    """Push protection precedes the push and ruleset. Other steps are independent."""
    calls.responses.update({"GET": "[]", "graphql": _graphql_projects({"number": 1, "title": "r"})})
    failed = github.configure_all("me/r", private=False, push=lambda: None, log=lambda _: None)
    assert failed == []
    paths = _paths(calls)
    settings_idx = paths.index("repos/me/r")
    ruleset_idx = paths.index("repos/me/r/rulesets?includes_parents=false")
    assert settings_idx < ruleset_idx
    fanout_paths = {
        "repos/me/r/vulnerability-alerts",
        "repos/me/r/automated-security-fixes",
        "repos/me/r/private-vulnerability-reporting",
        "repos/me/r/subscription",
    }
    fanout_indices = [i for i, p in enumerate(paths) if p in fanout_paths]
    assert len(fanout_indices) == len(fanout_paths)  # sanity: all of them actually ran
    assert all(settings_idx < i for i in fanout_indices)
    patch = next(b for a, b in calls if a[2] == "PATCH")
    assert "secret_scanning_push_protection" in patch["security_and_analysis"]
    mute = next(b for a, b in calls if a[3].endswith("/subscription"))
    assert mute == {"subscribed": False, "ignored": True}


def test_ruleset_does_not_wait_for_slow_project(calls, monkeypatch):
    project_started = threading.Event()
    project_finished = threading.Event()
    ruleset_started = threading.Event()

    def slow_project(_):
        project_started.set()
        ruleset_started.wait(0.5)
        project_finished.set()

    def check_ruleset(_):
        assert project_started.wait(0.5)
        assert not project_finished.is_set()
        ruleset_started.set()

    monkeypatch.setattr(github, "configure_project", slow_project)
    monkeypatch.setattr(github, "configure_ruleset", check_ruleset)
    failed = github.configure_all("me/r", private=False, push=lambda: None, log=lambda _: None)
    assert failed == []
    assert project_finished.is_set()


def test_push_failure_skips_ruleset_even_with_slow_project(calls, monkeypatch):
    project_started = threading.Event()

    def slow_project(_):
        project_started.set()
        time.sleep(0.1)

    monkeypatch.setattr(github, "configure_project", slow_project)
    failed = github.configure_all(
        "me/r",
        private=False,
        push=lambda: (_ for _ in ()).throw(RuntimeError("rejected")),
        log=lambda _: None,
    )
    assert failed == ["push main"]
    assert project_started.is_set()
    assert not any("rulesets" in path for path in _paths(calls))


def test_private_skips_paid_features(calls):
    github.configure_all("me/r", private=True, project=False, log=lambda _: None)
    paths = " ".join(_paths(calls))
    assert "rulesets" not in paths
    assert "private-vulnerability-reporting" not in paths
    patch = next(b for a, b in calls if a[2] == "PATCH")
    assert "security_and_analysis" not in patch


def test_private_disables_actions_before_push(calls):
    """No Actions minutes to spend on a private repo: disabled before the first
    push, so ci.yml/release.yml/Dependabot never get a chance to trigger."""
    order = []
    failed = github.configure_all(
        "me/r", private=True, project=False, push=lambda: order.append(len(calls)),
        log=lambda _: None,
    )  # fmt: skip
    assert failed == []
    paths = _paths(calls)
    disable_idx = paths.index("repos/me/r/actions/permissions")
    pushed_at = order[0]
    assert disable_idx < pushed_at
    disable_body = next(b for a, b in calls if a[3] == "repos/me/r/actions/permissions")
    assert disable_body == {"enabled": False}


def test_push_skipped_entirely_when_actions_cannot_be_disabled(calls):
    """A private repo must never reach GitHub with Actions still enabled: if
    disabling them fails, push isn't attempted at all, not just reported."""
    calls.fail_on = ("actions/permissions",)
    pushed = []
    failed = github.configure_all(
        "me/r", private=True, project=False, push=lambda: pushed.append(True), log=lambda _: None
    )
    assert "disable actions" in failed
    assert "push main" in failed
    assert not pushed  # the push callable itself must never run


def test_public_repo_leaves_actions_alone(calls):
    calls.responses.update({"GET": "[]", "graphql": _graphql_projects()})
    calls.responses["project create"] = '{"number": 1}'
    github.configure_all("me/r", private=False, log=lambda _: None)
    assert "repos/me/r/actions/permissions" not in _paths(calls)


def test_public_settings_failure_blocks_push_and_ruleset_but_allows_retry(calls):
    calls.responses["GET"] = "[]"
    calls.fail_on = ("api -X PATCH repos/me/r",)
    pushed = []
    failed = github.configure_all(
        "me/r", private=False, project=False, push=lambda: pushed.append(True), log=lambda _: None
    )
    assert failed == ["repo settings", "push main"]
    assert pushed == []
    assert not any("rulesets" in path for path in _paths(calls))
    assert "repos/me/r/subscription" in _paths(calls)

    calls.fail_on = ()
    failed = github.configure_all(
        "me/r", private=False, project=False, push=lambda: pushed.append(True), log=lambda _: None
    )
    assert failed == []
    assert pushed == [True]
    assert any("rulesets" in path for path in _paths(calls))


def test_public_settings_failure_still_repairs_ruleset_when_main_already_exists(calls):
    calls.responses["GET"] = "[]"
    calls.fail_on = ("api -X PATCH repos/me/r",)
    failed = github.configure_all("me/r", private=False, project=False, log=lambda _: None)
    assert failed == ["repo settings"]
    assert any("rulesets" in path for path in _paths(calls))


def test_failures_are_collected_and_others_still_run(calls):
    calls.responses["GET"] = "[]"
    calls.fail_on = ("automated-security-fixes",)
    failed = github.configure_all("me/r", private=False, project=False, log=lambda _: None)
    # Alerts and security-fixes are one task (enabling fixes before alerts can 422),
    # so a failure in either is reported under their combined name.
    assert failed == ["dependabot alerts + security fixes"]
    assert any("subscription" in p for p in _paths(calls))


def test_push_failure_stops_before_ruleset(calls):
    calls.responses["graphql"] = _graphql_projects({"number": 1, "title": "r"})

    def bad_push():
        raise RuntimeError("rejected")

    failed = github.configure_all("me/r", private=False, push=bad_push, log=lambda _: None)
    # push and project board are independent, concurrent fan-out steps: push
    # failing doesn't stop project board from running (it's already in flight),
    # it only skips the ruleset phase that comes strictly after the fan-out.
    assert failed == ["push main"]
    assert not any("rulesets" in p for p in _paths(calls))


def test_fanout_failure_order_is_declared_not_completion(monkeypatch):
    """A later-declared fan-out step failing faster than an earlier one must not
    reorder `failed` — pool.map preserves declared order, not completion order."""

    def fake_gh(*args, body=None):
        joined = " ".join(args)
        if "vulnerability-alerts" in joined:  # declared first, but slow
            time.sleep(0.2)
            raise github.GhError("alerts boom")
        if "subscription" in joined:  # declared later, fails immediately
            raise github.GhError("mute boom")
        return ""

    monkeypatch.setattr(github, "gh", fake_gh)
    failed = github.configure_all("me/r", private=True, project=False, log=lambda _: None)
    assert failed == ["dependabot alerts + security fixes", "mute notifications (watch: ignore)"]


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
