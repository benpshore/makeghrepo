import json
from copy import deepcopy

import pytest

from makeghrepo import github


@pytest.fixture(autouse=True)
def isolated_git(tmp_path_factory, monkeypatch):
    """Keep tests away from the user's global git config (signing, hooks, identity)."""
    cfg = tmp_path_factory.mktemp("gitcfg") / "gitconfig"
    cfg.write_text("[init]\n\tdefaultBranch = main\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test User")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.com")


class Calls(list):
    responses: dict
    fail_on: tuple


@pytest.fixture
def calls(monkeypatch):
    """Replace gh() with a recorder. Set calls.responses[substr] / calls.fail_on."""
    recorded = Calls()
    recorded.responses = {}
    recorded.fail_on = ()

    recorded.private = False
    state = {"settings": {}, "rulesets": {}, "labels": {}, "project": False}
    recorded.state = state

    def fake_gh(*args, body=None):
        recorded.append((args, deepcopy(body)))
        joined = " ".join(args)
        if any(f in joined for f in recorded.fail_on):
            raise github.GhError(f"boom: {joined}")
        if args == ("api", "--include", "--silent", "user"):
            return "X-OAuth-Scopes: repo, workflow, project"
        if args == ("api", "-X", "GET", "user"):
            return '{"login": "me", "id": 123}'
        if args[:2] == ("project", "list"):
            return next(
                (v for k, v in recorded.responses.items() if k in joined),
                json.dumps({"projects": []}),
            )
        if args[:2] == ("label", "create"):
            state["labels"][args[2]] = args[args.index("--color") + 1]
            return ""
        if args[:2] == ("project", "link"):
            state["project"] = True
            return ""
        if args[:2] == ("api", "graphql") and "projectV2(number:" in joined:
            return json.dumps(
                {
                    "data": {
                        "user": {
                            "projectV2": {
                                "title": "r",
                                "repositories": {
                                    "nodes": [{"nameWithOwner": "me/r"}]
                                    if state["project"]
                                    else [],
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                },
                            }
                        }
                    }
                }
            )
        if args[:2] == ("api", "graphql"):
            return next(
                (v for k, v in recorded.responses.items() if k in joined),
                json.dumps(
                    {"data": {"user": {"projectsV2": {"nodes": [{"number": 1, "title": "r"}]}}}}
                ),
            )
        if args[:2] == ("api", "-X"):
            method, path = args[2:4]
            if path == "repos/me/r":
                if method == "PATCH":
                    state["settings"] = deepcopy(body)
                return json.dumps(
                    {
                        "private": recorded.private,
                        "owner": {"type": "User", "login": "me", "id": 123},
                        "permissions": {"admin": True},
                        "default_branch": "develop",
                        **state["settings"],
                    }
                )
            if "/rulesets" in path:
                if method == "GET":
                    if "?" in path:
                        return json.dumps(list(state["rulesets"].values()))
                    return json.dumps(state["rulesets"][int(path.rsplit("/", 1)[1])])
                number = (
                    int(path.rsplit("/", 1)[1]) if method == "PUT" else len(state["rulesets"]) + 1
                )
                state["rulesets"][number] = {"id": number, **deepcopy(body)}
                return "{}"
            if method == "PUT":
                state[path] = deepcopy(body) if body is not None else {"enabled": True}
            if method == "GET":
                if "/labels/" in path:
                    name = path.rsplit("/", 1)[1]
                    return json.dumps({"name": name, "color": state["labels"][name]})
                if path.endswith("vulnerability-alerts"):
                    return ""
                if path.endswith("automated-security-fixes"):
                    return json.dumps({**state.get(path, {}), "paused": False})
                return json.dumps(state[path])
        return next((v for k, v in recorded.responses.items() if k in joined), "")

    monkeypatch.setattr(github, "gh", fake_gh)
    return recorded
