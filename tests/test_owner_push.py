"""Migration regressions also run offline with stdlib unittest."""

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from makeghrepo import github


class RulesetAPI:
    """Stateful API double; failures occur before a policy write takes effect."""

    def __init__(self, existing, *, owner=None, fail_write=0):
        self.state = copy.deepcopy(existing)
        self.owner = {"login": "me", "id": 123, "type": "User"} if owner is None else owner
        self.fail_write = fail_write
        self.calls = []

    @property
    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        if method == "GET":
            if path == "repos/me/r":
                return {"owner": self.owner}
            if "?" in path:
                page = int(path.split("page=")[-1])
                return [{"id": id_, "name": value["name"]} for id_, value in self.state.items()][
                    (page - 1) * 100 : page * 100
                ]
            return copy.deepcopy(self.state[int(path.rsplit("/", 1)[1])])
        if self.fail_write and len(self.writes) == self.fail_write:
            raise github.GhError("write rejected")
        id_ = int(path.rsplit("/", 1)[1]) if method == "PUT" else max(self.state, default=0) + 1
        self.state[id_] = copy.deepcopy(body)
        return {"id": id_}


class OwnerPushTests(unittest.TestCase):
    def setUp(self):
        self.defaults = json.loads(
            (Path(__file__).parent / "fixtures/github_defaults.json").read_text()
        )
        self.history = self.defaults["ruleset"]
        self.review = self.defaults["review_ruleset"]

    def legacy(self):
        return {
            **copy.deepcopy(self.history),
            "rules": copy.deepcopy(self.history["rules"] + self.review["rules"]),
        }

    def test_owner_only_bypass_survives_reruns(self):
        for mode in ("new", "legacy", "split", "partial"):
            with self.subTest(mode=mode):
                existing = {7: {"name": "another-rule"}}
                if mode != "new":
                    existing[42] = self.history if mode == "split" else self.legacy()
                if mode in ("split", "partial"):
                    existing[43] = self.review
                api = RulesetAPI(existing)
                with patch.object(github, "api", api):
                    for _ in range(2):
                        api.calls.clear()
                        github.configure_ruleset("me/r")
                        assert [c[2] for c in api.writes] == [self.review, self.history]
                        assert {v["name"] for v in api.state.values()} == {
                            "another-rule",
                            "protect-main",
                            "require-pr-and-ci",
                        }
                        assert api.state[7] == existing[7]
                assert [c[0] for c in api.writes] == ["PUT", "PUT"]

    def test_preserves_extra_checks_and_unrelated_rules(self):
        legacy = self.legacy()
        legacy["rules"][3]["parameters"]["require_extra_approval_for_unattributed_changes"] = True
        legacy["rules"][4]["parameters"]["required_status_checks"].append({"context": "security"})
        legacy["rules"].append({"type": "required_signatures"})
        api = RulesetAPI({42: legacy})
        with patch.object(github, "api", api):
            github.configure_ruleset("me/r")
            assert api.writes[0][2]["rules"] == legacy["rules"][3:5]
            assert api.writes[1][2]["rules"] == legacy["rules"][:3] + legacy["rules"][5:]
            assert api.writes[1][2]["bypass_actors"] == []
            expected = copy.deepcopy(api.state)
            github.configure_ruleset("me/r")
            assert api.state == expected

    def test_failed_migration_retains_legacy_gate(self):
        for failure in (1, 2):
            with self.subTest(failure=failure):
                legacy = self.legacy()
                api = RulesetAPI({42: legacy}, fail_write=failure)
                with (
                    patch.object(github, "api", api),
                    self.assertRaisesRegex(github.GhError, "write rejected"),
                ):
                    github.configure_ruleset("me/r")
                assert api.state[42] == legacy
                assert len(api.writes) == failure

    def test_unverified_owner_never_grants_bypass(self):
        owners = [
            {"type": "Organization", "login": "me", "id": 123},
            {"type": "User", "login": "someone-else", "id": 123},
            {"type": "User", "login": "me"},
            {"type": "User", "login": "me", "id": True},
            {"type": "User", "login": "me", "id": 0},
        ]
        for owner in owners:
            with self.subTest(owner=owner):
                api = RulesetAPI({}, owner=owner)
                with (
                    patch.object(github, "api", api),
                    self.assertRaisesRegex(github.GhError, "verified owner ID"),
                ):
                    github.configure_ruleset("me/r")
                assert not api.writes

    def test_ambiguous_policy_refused_before_writes(self):
        for customization in (
            "scope",
            "enforcement",
            "bypass",
            "hidden_bypass",
            "duplicate",
            "conflict",
            "extra_gate",
        ):
            with self.subTest(customization=customization):
                legacy = self.legacy()
                review = copy.deepcopy(self.review)
                existing = {42: legacy, 43: review}
                if customization == "scope":
                    legacy["conditions"]["ref_name"]["include"].append("refs/heads/release/*")
                elif customization == "enforcement":
                    legacy["enforcement"] = "disabled"
                elif customization == "bypass":
                    legacy["bypass_actors"] = [{"actor_type": "RepositoryRole", "actor_id": 5}]
                elif customization == "hidden_bypass":
                    del legacy["bypass_actors"]
                elif customization == "duplicate":
                    existing[44] = legacy
                elif customization == "conflict":
                    review["rules"][0]["parameters"]["required_approving_review_count"] = 2
                else:
                    review["rules"].append({"type": "required_signatures"})
                api = RulesetAPI(existing)
                with patch.object(github, "api", api), self.assertRaises(github.GhError):
                    github.configure_ruleset("me/r")
                assert not api.writes

    def test_ruleset_listing_is_paginated(self):
        existing = {i: {"name": f"other-{i}"} for i in range(100)}
        existing[142] = self.legacy()
        api = RulesetAPI(existing)
        with patch.object(github, "api", api):
            github.configure_ruleset("me/r")
        assert any("page=2" in c[1] for c in api.calls)
        assert api.calls[-1][:2] == ("PUT", "repos/me/r/rulesets/142")
