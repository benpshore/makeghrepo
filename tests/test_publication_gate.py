"""Publication gates use stateful offline API fixtures and explicit thread barriers."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from makeghrepo import github

PUBLIC_WRITES = [
    "api -X PATCH repos/me/r",
    "api -X POST repos/me/r/rulesets",
    "api -X PUT repos/me/r/vulnerability-alerts",
    "api -X PUT repos/me/r/automated-security-fixes",
    "api -X PUT repos/me/r/private-vulnerability-reporting",
    "label create epic",
    "api -X PUT repos/me/r/subscription",
    "project link",
]
PRIVATE_WRITES = [
    PUBLIC_WRITES[0],
    "api -X PUT repos/me/r/actions/permissions",
    *PUBLIC_WRITES[2:4],
    *PUBLIC_WRITES[5:],
]


@pytest.mark.parametrize(
    "private,operation",
    [
        *((False, op) for op in PUBLIC_WRITES),
        *((True, op) for op in PRIVATE_WRITES),
    ],
)
@pytest.mark.parametrize("fail", [False, True])
def test_required_write_must_settle_before_publication(
    calls, monkeypatch, private, operation, fail
):
    calls.private = private
    original = github.gh
    entered = threading.Event()
    release = threading.Event()
    pushed = threading.Event()

    def delayed(*args, body=None):
        if operation in " ".join(args):
            entered.set()
            assert release.wait(5), "test did not release required operation"
            if fail:
                raise github.GhError("controlled post-creation failure")
        return original(*args, body=body)

    monkeypatch.setattr(github, "gh", delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(
            github.configure_all, "me/r", private=private, push=pushed.set, log=lambda _: None
        )
        try:
            assert entered.wait(5), "required operation was never reached"
            assert not pushed.is_set()
        finally:
            release.set()
        failed = result.result(timeout=5)
    assert pushed.is_set() is (not fail)
    assert ("push main" in failed) is fail
    if fail:
        # Earlier writes remain; retry reuses those resources without deletion.
        before = deepcopy(calls.state)
        monkeypatch.setattr(github, "gh", original)
        assert (
            github.configure_all("me/r", private=private, push=pushed.set, log=lambda _: None) == []
        )
        assert pushed.is_set()
        assert len(calls.state["rulesets"]) == (0 if private else 2)
        assert before["settings"] or operation == PUBLIC_WRITES[0]
        assert not any("delete" in args for args, _ in calls)


@pytest.mark.parametrize(
    "operation",
    [
        "api -X GET repos/me/r/automated-security-fixes",
        "api -X GET repos/me/r/private-vulnerability-reporting",
        "api -X GET repos/me/r/labels/epic",
        "api -X GET repos/me/r/subscription",
        "projectV2(number:",
    ],
)
def test_delayed_or_failed_readback_blocks_push(calls, monkeypatch, operation):
    original = github.gh
    entered, release, pushed = threading.Event(), threading.Event(), threading.Event()

    def delayed(*args, body=None):
        if operation in " ".join(args):
            entered.set()
            assert release.wait(5)
            raise github.GhError("readback unavailable")
        return original(*args, body=body)

    monkeypatch.setattr(github, "gh", delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(
            github.configure_all, "me/r", private=False, push=pushed.set, log=lambda _: None
        )
        try:
            assert entered.wait(5)
            assert not pushed.is_set()
        finally:
            release.set()
        assert "push main" in result.result(timeout=5)
    assert not pushed.is_set()


@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize("field", list(github.settings_body(False)))
def test_successful_patch_with_discrepant_effective_settings_blocks_push(
    calls,
    monkeypatch,
    private,
    field,
):
    if private and field in {"security_and_analysis", "allow_auto_merge"}:
        pytest.skip("not required by the documented private policy")
    calls.private = private
    original = github.gh

    def ignored(*args, body=None):
        result = original(*args, body=body)
        if args[:4] == ("api", "-X", "PATCH", "repos/me/r"):
            calls.state["settings"].pop(field)
        return result

    monkeypatch.setattr(github, "gh", ignored)
    pushed = []
    failed = github.configure_all(
        "me/r", private=private, push=lambda: pushed.append(True), log=lambda _: None
    )
    assert "repo settings readback" in failed and "push main" in failed
    assert not pushed


@pytest.mark.parametrize(
    "private,path,response",
    [
        (True, "actions/permissions", '{"enabled": true}'),
        (False, "automated-security-fixes", '{"enabled": false, "paused": false}'),
        (False, "automated-security-fixes", '{"enabled": true, "paused": true}'),
        (False, "private-vulnerability-reporting", '{"enabled": false}'),
        (False, "subscription", '{"subscribed": true, "ignored": false}'),
        (False, "labels/epic", '{"name": "epic", "color": "000000"}'),
        (False, "labels/task", "{}"),
        (False, "subscription", "not JSON"),
    ],
)
def test_successful_writes_do_not_substitute_for_readback(
    calls, monkeypatch, private, path, response
):
    calls.private = private
    original = github.gh

    def discrepant(*args, body=None):
        if args[:4] == ("api", "-X", "GET", "repos/me/r/" + path):
            return response
        return original(*args, body=body)

    monkeypatch.setattr(github, "gh", discrepant)
    pushed = []
    failed = github.configure_all(
        "me/r", private=private, push=lambda: pushed.append(True), log=lambda _: None
    )
    assert "push main" in failed and any("readback" in name for name in failed)
    assert not pushed


def test_disabled_alerts_404_blocks_publication(calls):
    calls.fail_on = ("api -X GET repos/me/r/vulnerability-alerts",)
    pushed = []
    assert "push main" in github.configure_all(
        "me/r", private=False, push=lambda: pushed.append(True), log=lambda _: None
    )
    assert not pushed


def test_project_link_readback_and_retry_reuses_board(calls, monkeypatch):
    original = github.gh

    def unlinked(*args, body=None):
        result = original(*args, body=body)
        if args[:2] == ("project", "link"):
            calls.state["project"] = False
        return result

    monkeypatch.setattr(github, "gh", unlinked)
    pushed = []
    failed = github.configure_all(
        "me/r", private=False, push=lambda: pushed.append(True), log=lambda _: None
    )
    assert "project board readback" in failed and not pushed
    monkeypatch.setattr(github, "gh", original)
    assert (
        github.configure_all(
            "me/r", private=False, push=lambda: pushed.append(True), log=lambda _: None
        )
        == []
    )
    assert pushed == [True]
    assert not any(args[:2] == ("project", "create") for args, _ in calls)


def test_project_not_selected_has_no_project_prerequisite_or_side_effect(calls):
    calls.fail_on = ("project", "graphql")
    plan = github.prepare_configuration("me", False, project=False)
    assert (
        github.configure_all(
            "me/r",
            private=False,
            project=False,
            configuration=plan,
            push=lambda: None,
            log=lambda _: None,
        )
        == []
    )
    assert not any("project" in " ".join(args) for args, _ in calls)


@pytest.mark.parametrize("failure", ["api -X GET user", "--include --silent user", "project list"])
def test_preflight_checks_required_access_without_mutation(calls, failure):
    calls.fail_on = (failure,)
    with pytest.raises(github.GhError):
        github.prepare_configuration("me", False)
    assert not calls.state["settings"] and not calls.state["rulesets"]
    assert not any(args[:2] == ("repo", "create") for args, _ in calls)


@pytest.mark.parametrize(
    "scopes,private,project",
    [
        ("repo, workflow", False, True),
        ("repo, project", False, True),
        ("public_repo, workflow, project", True, True),
        ("workflow, project", False, True),
    ],
)
def test_known_missing_scopes_fail_preflight(calls, monkeypatch, scopes, private, project):
    original = github.gh

    def scoped(*args, body=None):
        if args == ("api", "--include", "--silent", "user"):
            return "HTTP/2 200 OK\nX-OAuth-Scopes: " + scopes
        return original(*args, body=body)

    monkeypatch.setattr(github, "gh", scoped)
    with pytest.raises(github.GhError, match="missing GitHub token scopes"):
        github.prepare_configuration("me", private, project=project)


@pytest.mark.parametrize("change", ["visibility", "owner", "admin"])
def test_partial_remote_identity_or_permissions_fail_before_writes(calls, monkeypatch, change):
    plan = github.prepare_configuration("me", False)
    original = github.gh
    start = len(calls)

    def changed(*args, body=None):
        result = original(*args, body=body)
        if args[:4] == ("api", "-X", "GET", "repos/me/r"):
            saved = json.loads(result)
            if change == "visibility":
                saved["private"] = True
            elif change == "owner":
                saved["owner"]["id"] = 456
            else:
                saved["permissions"]["admin"] = False
            return json.dumps(saved)
        return result

    monkeypatch.setattr(github, "gh", changed)
    pushed = []
    assert github.configure_all(
        "me/r",
        private=False,
        configuration=plan,
        push=lambda: pushed.append(True),
        log=lambda _: None,
    ) == [
        "repository access",
        "push main",
    ]
    assert not pushed
    assert all(args[:3] == ("api", "-X", "GET") for args, _ in calls[start:])


def test_private_auto_merge_is_explicitly_advisory(calls, monkeypatch):
    calls.private = True
    original = github.gh

    def unavailable(*args, body=None):
        result = original(*args, body=body)
        if args[:4] == ("api", "-X", "PATCH", "repos/me/r"):
            calls.state["settings"]["allow_auto_merge"] = False
        return result

    monkeypatch.setattr(github, "gh", unavailable)
    logs = []
    assert github.configure_all("me/r", private=True, push=lambda: None, log=logs.append) == []
    assert any("auto-merge is advisory" in line for line in logs)


@pytest.mark.parametrize("change", ["enforcement", "scope", "bypass", "history", "checks"])
def test_successful_ruleset_write_requires_effective_policy(calls, monkeypatch, change):
    original = github.gh

    def changed(*args, body=None):
        result = original(*args, body=body)
        if args[:3] == ("api", "-X", "POST") and args[3].endswith("/rulesets"):
            saved = calls.state["rulesets"][max(calls.state["rulesets"])]
            if change == "enforcement":
                saved["enforcement"] = "disabled"
            elif change == "scope":
                saved["conditions"]["ref_name"]["include"].remove(github.MAIN_REF)
            elif change == "bypass":
                saved["bypass_actors"].append({"actor_type": "RepositoryRole", "actor_id": 5})
            elif change == "history" and saved["name"] == github.RULESET_NAME:
                saved["rules"].pop()
            elif change == "checks" and saved["name"] == github.REVIEW_RULESET_NAME:
                saved["rules"][1]["parameters"]["required_status_checks"][0]["integration_id"] = 1
        return result

    monkeypatch.setattr(github, "gh", changed)
    pushed = []
    failed = github.configure_all(
        "me/r", private=False, push=lambda: pushed.append(True), log=lambda _: None
    )
    assert "push main" in failed and not pushed


def test_final_readback_detects_protection_changed_by_later_operation(calls, monkeypatch):
    original = github.gh

    def drift(*args, body=None):
        result = original(*args, body=body)
        if args[:2] == ("project", "link"):
            next(r for r in calls.state["rulesets"].values() if r["name"] == github.RULESET_NAME)[
                "enforcement"
            ] = "disabled"
        return result

    monkeypatch.setattr(github, "gh", drift)
    pushed = []
    failed = github.configure_all(
        "me/r", private=False, push=lambda: pushed.append(True), log=lambda _: None
    )
    assert "ruleset 'protect-main' readback" in failed and not pushed


def test_failed_push_retains_settings_and_retry_is_idempotent(calls):
    def rejected():
        raise github.GhError("transport rejected")

    assert github.configure_all("me/r", private=False, push=rejected, log=lambda _: None) == [
        "push main"
    ]
    before = deepcopy(calls.state)
    pushed = []
    assert (
        github.configure_all(
            "me/r", private=False, push=lambda: pushed.append(True), log=lambda _: None
        )
        == []
    )
    assert calls.state == before and pushed == [True]


def test_mismatched_selected_plan_refused_before_writes(calls):
    plan = github.prepare_configuration("me", False, project=False)
    before = len(calls)
    with pytest.raises(github.GhError, match="selected options"):
        github.configure_all("me/r", private=False, project=True, configuration=plan)
    assert len(calls) == before


def test_project_retry_refuses_truncated_listing_instead_of_creating_duplicate(calls):
    calls.responses["graphql"] = json.dumps(
        {
            "data": {
                "user": {
                    "projectsV2": {
                        "nodes": [{"number": i, "title": f"other-{i}"} for i in range(100)],
                    }
                }
            }
        }
    )
    calls.responses["project list"] = json.dumps(
        {
            "projects": [{"number": i, "title": f"other-{i}"} for i in range(1000)],
            "totalCount": 1001,
        }
    )
    with pytest.raises(github.GhError, match="incomplete"):
        github.configure_project("me/r")
    assert not any(args[:2] == ("project", "create") for args, _ in calls)


def test_project_link_readback_searches_later_repository_pages(calls, monkeypatch):
    cursors = []

    def pages(*args, body=None):
        cursor = next((arg for arg in args if arg.startswith("cursor=")), None)
        cursors.append(cursor)
        repos = {
            "nodes": [] if cursor is None else [{"nameWithOwner": "me/r"}],
            "pageInfo": {"hasNextPage": cursor is None, "endCursor": "next"},
        }
        return json.dumps(
            {
                "data": {
                    "user": {
                        "projectV2": {
                            "title": "r",
                            "repositories": repos,
                        }
                    }
                }
            }
        )

    monkeypatch.setattr(github, "gh", pages)
    github.verify_project("me/r", 1)
    assert cursors == [None, "cursor=next"]
