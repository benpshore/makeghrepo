"""Offline policy regressions: shared matrix plus fail-closed live rules."""

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from test_owner_push import RulesetAPI

from makeghrepo import github

MATRIX = json.loads((Path(__file__).parent / "fixtures/creation_policy.json").read_text())


class CreationPolicyTests(unittest.TestCase):
    def test_shared_matrix_and_idempotent_rules(self):
        for case in MATRIX:
            with self.subTest(case=case):
                flags = {key: case[key] for key in ("no_ci", "no_pr")}
                body = github.review_ruleset_body(123, **flags)
                assert [rule["type"] for rule in body["rules"]] == case["rules"]
                assert body["bypass_actors"] == [
                    {"actor_type": "User", "actor_id": 123, "bypass_mode": "always"}
                ]
                api = RulesetAPI({7: {"name": "custom-policy", "rules": [{"type": "creation"}]}})
                with patch.object(github, "api", api):
                    github.configure_ruleset("me/r", **flags, validate_only=True)
                    assert not api.writes
                    for _ in range(2):
                        github.configure_ruleset("me/r", **flags)
                        rulesets = {v["name"]: v for v in api.state.values()}
                        assert rulesets["protect-main"] == github.ruleset_body()
                        assert ("require-pr-and-ci" in rulesets) is bool(case["rules"])
                        if case["rules"]:
                            assert rulesets["require-pr-and-ci"] == body
                        assert api.state[7]["rules"] == [{"type": "creation"}]
                        assert all(call[2]["rules"] for call in api.writes)

    def test_opt_out_refuses_live_split_and_legacy_requirements_before_writes(self):
        for case in MATRIX[1:]:
            flags = {key: case[key] for key in ("no_ci", "no_pr")}
            for legacy in (False, True):
                history, gate = github.ruleset_body(), github.review_ruleset_body(123)
                if legacy:
                    history["rules"] += gate["rules"]
                api = RulesetAPI({42: history} if legacy else {42: history, 43: gate})
                before = copy.deepcopy(api.state)
                with patch.object(github, "api", api), self.assertRaises(github.GhError):
                    github.configure_ruleset("me/r", **flags)
                assert not api.writes
                assert api.state == before

    def test_stricter_remaining_gate_is_preserved(self):
        for no_ci, no_pr in ((True, False), (False, True)):
            body = github.review_ruleset_body(123, no_ci=no_ci, no_pr=no_pr)
            parameters = body["rules"][0]["parameters"]
            if no_ci:
                parameters["required_approving_review_count"] = 2
            else:
                parameters["required_status_checks"].append({"context": "security"})
            api = RulesetAPI({42: github.ruleset_body(), 43: body})
            with patch.object(github, "api", api):
                github.configure_ruleset("me/r", no_ci=no_ci, no_pr=no_pr)
            assert api.state[43] == body

    def test_opt_out_preflight_stops_all_configuration_and_publishing(self):
        for case in MATRIX[1:]:
            api = RulesetAPI({42: github.ruleset_body(), 43: github.review_ruleset_body(123)})
            pushes = []
            with patch.object(github, "api", api):
                failed = github.configure_all(
                    "me/r",
                    private=False,
                    no_ci=case["no_ci"],
                    no_pr=case["no_pr"],
                    push=lambda: pushes.append(True),
                    log=lambda _: None,
                )
            assert failed == ["creation policy"]
            assert not pushes and not api.writes

    def test_private_policy_rejected_without_api_calls(self):
        for case in MATRIX[1:]:
            with patch.object(github, "api") as api, self.assertRaises(github.GhError):
                github.configure_all("me/r", private=True, no_ci=case["no_ci"], no_pr=case["no_pr"])
            api.assert_not_called()
