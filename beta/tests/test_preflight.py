"""Read-only bootstrap inspection and shared validation contracts."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from makeghrepo_beta import cli, preflight
from makeghrepo_beta.plan import BootstrapPlan, Request, build_plan


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def command(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cli.main(list(args))
        return output.getvalue()

    def test_plain_bootstrap_dry_run_needs_no_subprocess_or_writes(self):
        with (
            patch("subprocess.Popen", side_effect=AssertionError("unexpected process")),
            patch("os.mkdir", side_effect=AssertionError("unexpected write")),
        ):
            report = json.loads(
                self.command("sample", "py", "-p", "--root", str(self.root), "-n", "--json")
            )
        self.assertEqual(report["inputs"]["languages"], ["python"])
        self.assertIsNone(report["inputs"]["owner"])
        self.assertIsNone(report["destination"])
        self.assertFalse(report["execution_available"])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_preflight_consumes_the_shared_plan_without_writes(self):
        seen = []
        expected = build_plan(Request(words=("My Sample", "py"), private=True, root=self.root))

        real_inspect = preflight.inspect

        def observed(plan):
            seen.append(plan)
            return real_inspect(
                plan,
                lambda endpoint: {"login": "account", "id": 123} if endpoint == "user" else {},
            )

        with patch("makeghrepo_beta.preflight.inspect", observed):
            report = json.loads(
                self.command(
                    "My Sample", "py", "-p", "--root", str(self.root), "--preflight", "--json"
                )
            )
        self.assertEqual(seen, [expected])
        self.assertEqual(report["destination"], str(self.root / "account/private/my-sample"))
        self.assertEqual(report["repository_url"], "https://github.com/account/my-sample")
        self.assertFalse(report["execution_available"])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_validation_matches_and_precedes_every_probe(self):
        cases = (
            ("../sample",),
            ("sample", "unknown"),
            ("sample", "--owner", "../bad"),
            ("python",),
        )
        for inputs in cases:
            results = []
            for mode in ("--dry-run", "--preflight"):
                error = io.StringIO()
                with (
                    self.subTest(inputs=inputs, mode=mode),
                    patch("makeghrepo_beta.preflight.gh_read", side_effect=AssertionError("probe")),
                    contextlib.redirect_stderr(error),
                    self.assertRaises(SystemExit) as caught,
                ):
                    cli.main([*inputs, "--root", str(self.root), mode])
                results.append((caught.exception.code, error.getvalue()))
            self.assertEqual(results[0], results[1])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_unavailable_malformed_and_changed_identity_are_unknown(self):
        plan = build_plan(Request(words=("sample",), root=self.root))
        users = (None, [], {}, {"id": True, "login": "account"}, {"id": 123, "login": "../bad"})
        for user in users:
            with self.subTest(user=user):
                calls = []

                def read(endpoint, user=user, calls=calls):
                    calls.append(endpoint)
                    return user

                report = preflight.inspect(plan, read)
                self.assertIsNone(report["destination"])
                self.assertEqual(calls, ["user"])
                self.assertEqual(report["observations"][0]["status"], "unknown")

    def test_failed_lookup_never_means_absent_or_available(self):
        plan = build_plan(Request(words=("sample",), owner="organization", root=self.root))

        def read(endpoint):
            if endpoint == "user":
                return {"id": 123, "login": "account"}
            raise RuntimeError("403 or 404: SENSITIVE_BACKEND_DETAIL")

        report = preflight.inspect(plan, read)
        states = {item["capability"]: item["status"] for item in report["observations"]}
        self.assertEqual(states["owner_policy"], "unknown")
        self.assertEqual(states["target"], "unknown")
        self.assertNotIn("SENSITIVE_BACKEND_DETAIL", json.dumps(report))
        self.assertEqual(states["live_execution"], "unsupported")

    def test_existing_repository_permission_does_not_authorize_creation(self):
        plan = build_plan(Request(words=("sample",), owner="account", root=self.root))
        report = preflight.inspect(
            plan,
            lambda endpoint: {"id": 123, "login": "account"}
            if endpoint == "user"
            else {"id": 456, "full_name": "account/sample", "permissions": {"admin": False}},
        )
        states = {item["capability"]: item["status"] for item in report["observations"]}
        self.assertEqual(states["target"], "observed")
        self.assertEqual(states["existing_repository_admin"], "denied")
        self.assertEqual(states["repository_creation"], "unknown")
        self.assertFalse(report["execution_available"])

    def test_transport_has_fixed_get_method_and_sanitized_errors(self):
        result = subprocess.CompletedProcess([], 0, '{"id":123}', "")
        with patch("subprocess.run", return_value=result) as run:
            self.assertEqual(preflight.gh_read("user"), {"id": 123})
        self.assertEqual(
            run.call_args.args[0],
            ["gh", "api", "--hostname", "github.com", "--method", "GET", "user"],
        )
        result.returncode = 1
        result.stderr = "SENSITIVE"
        with (
            patch("subprocess.run", return_value=result),
            self.assertRaises(RuntimeError) as caught,
        ):
            preflight.gh_read("user")
        self.assertNotIn("SENSITIVE", str(caught.exception))

    def test_live_execution_and_conflicting_modes_fail_before_probes(self):
        for args in (
            ["sample"],
            ["sample", "--preflight", "-n"],
            ["sample", "--render", str(self.root / "preview"), "--preflight"],
            ["--status", str(self.root), "sample"],
        ):
            with (
                self.subTest(args=args),
                patch("makeghrepo_beta.preflight.gh_read", side_effect=AssertionError("probe")),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                cli.main(args)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_phone_help_and_json_are_plain_and_consistent(self):
        plan = build_plan(Request(words=("sample",), root=self.root))
        self.assertIsInstance(plan, BootstrapPlan)
        for columns in (32, 40, 80):
            with self.subTest(columns=columns), patch.dict(os.environ, {"COLUMNS": str(columns)}):
                help_text = cli.parser().format_help()
                self.assertLessEqual(max(map(len, help_text.splitlines())), columns)
                for flag in ("--dry-run", "--preflight", "--status", "--root", "-p"):
                    self.assertIn(flag, help_text)
                self.assertNotIn("\x1b", help_text)
                text = self.command("sample", "--root", str(self.root), "-n", "--json")
                self.assertEqual(json.loads(text), plan.as_dict())

    def test_cli_preflight_with_real_fake_gh_process(self):
        probes = self.root / "bin"
        probes.mkdir()
        fake = probes / "gh"
        fake.write_text(
            f"#!{sys.executable}\n"
            "import json, sys\n"
            "prefix = ['api', '--hostname', 'github.com', '--method', 'GET']\n"
            "if sys.argv[1:-1] != prefix:\n"
            "    sys.exit(99)\n"
            "if sys.argv[-1] == 'user':\n"
            "    print(json.dumps({'id': 123, 'login': 'fixture-account'}))\n"
            "    sys.exit(0)\n"
            "sys.exit(1)\n"
        )
        fake.chmod(0o700)
        destination_root = self.root / "uncreated"
        result = subprocess.run(
            [
                sys.executable, "-B", "-m", "makeghrepo_beta.cli", "sample", "--preflight",
                "--root", str(destination_root), "--json",
            ],
            env={**os.environ, "PATH": str(probes), "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["inputs"]["owner"], "fixture-account")
        self.assertFalse(destination_root.exists())
        self.assertEqual(list(self.root.iterdir()), [probes])


if __name__ == "__main__":
    unittest.main()
