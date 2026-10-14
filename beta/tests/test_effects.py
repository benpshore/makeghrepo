"""Adversarial CLI and exception-boundary tests without external dependencies."""

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

from makeghrepo_beta import cli
from makeghrepo_beta.execute import ExecutionError, execute
from makeghrepo_beta.plan import Request, build_plan, directory_state


class EffectBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dest = self.root / "new" / "preview"
        self.request = Request(words=("sample", "python"), render=self.dest)

    @staticmethod
    def render(path, data):
        (path / "README.md").write_text(data["project_name"])

    def test_invalid_options_and_values_match_in_dry_run_and_execution(self):
        cases = [
            ["sample", "--unknown"],
            ["sample", "--rend", str(self.dest)],
            ["sample", "--dry"],
            ["sample", "--all"],
            ["sample", "--license", "mit"],
            ["sample", "--year", "bad"],
            ["sample", "--owner", "../other"],
            ["sample", "--lib"],
            ["7up", "rust"],
            ["sample", "python", "unrecognized"],
        ]
        with (
            patch("makeghrepo_beta.execute.execute") as effect,
            patch("subprocess.Popen", side_effect=AssertionError("external command")),
        ):
            for args in cases:
                observations = []
                for dry in (False, True):
                    stderr = io.StringIO()
                    command = [*args, "--render", str(self.dest)]
                    if dry:
                        command.append("--dry-run")
                    with (
                        self.subTest(args=args, dry_run=dry),
                        contextlib.redirect_stderr(stderr),
                        self.assertRaises(SystemExit) as caught,
                    ):
                        cli.main(command)
                    observations.append((caught.exception.code, stderr.getvalue().splitlines()[-1]))
                self.assertEqual(observations[0], observations[1])
            effect.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_flags_can_be_interleaved_with_positional_languages(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cli.main(
                ["sample", "-p", "python", "--lib", "--render", str(self.dest), "-n", "--json"]
            )
        plan = json.loads(output.getvalue())
        self.assertEqual(plan["inputs"]["languages"], ["python"])
        self.assertTrue(plan["inputs"]["private"])
        self.assertTrue(plan["inputs"]["py_lib"])
        self.assertFalse(self.dest.exists())

    def test_repeated_dry_run_is_deterministic_and_preserves_directory_metadata(self):
        self.dest.mkdir(parents=True)
        before = self.dest.stat()
        outputs = []
        for _ in range(2):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                cli.main(["sample", "python", "--render", str(self.dest), "-n", "--json"])
            outputs.append(output.getvalue())
        after = self.dest.stat()
        self.assertEqual(outputs[0], outputs[1])
        original_state = (before.st_ino, before.st_mode, before.st_mtime_ns)
        current_state = (after.st_ino, after.st_mode, after.st_mtime_ns)
        self.assertEqual(original_state, current_state)
        self.assertEqual(list(self.dest.parent.iterdir()), [self.dest])
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_retry_after_failure_preserves_empty_target_then_succeeds(self):
        self.dest.mkdir(parents=True)
        plan = build_plan(self.request)
        original = directory_state(self.dest)

        def failure(path, data):
            self.render(path, data)
            raise ValueError("template failed")

        for _ in range(2):
            with self.assertRaises(ExecutionError) as caught:
                execute(plan, failure)
            self.assertEqual(caught.exception.phase, "render templates")
            self.assertEqual(caught.exception.outcome, "not installed")
            self.assertEqual(directory_state(self.dest), original)
            self.assertEqual(list(self.dest.iterdir()), [])
            self.assertEqual(list(self.dest.parent.iterdir()), [self.dest])
        execute(plan, self.render)
        self.assertEqual((self.dest / "README.md").read_text(), "sample")

    def test_retry_after_success_refuses_to_overwrite_work(self):
        plan = build_plan(self.request)
        execute(plan, self.render)
        (self.dest / "README.md").write_text("user edited after success")
        with self.assertRaisesRegex(ValueError, "not empty"):
            execute(plan, self.render)
        self.assertEqual((self.dest / "README.md").read_text(), "user edited after success")
        self.assertEqual(list(self.dest.parent.iterdir()), [self.dest])

    def test_separate_process_is_refused_while_writer_lock_is_held(self):
        def render(path, data):
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "makeghrepo_beta.cli",
                    "sample",
                    "python",
                    "--render",
                    str(self.dest),
                ],
                env={**os.environ, "PATH": ""},
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("another render may be active", result.stderr)
            self.assertIn("acquire writer lock", result.stderr)
            self.render(path, data)

        execute(build_plan(self.request), render)
        self.assertEqual(list(self.dest.parent.iterdir()), [self.dest])

    def test_failure_after_rename_reports_installation_and_preserves_output(self):
        original_replace = os.replace

        def interrupted_replace(source, destination):
            original_replace(source, destination)
            raise OSError("response lost after rename")

        with (
            patch("makeghrepo_beta.execute.os.replace", interrupted_replace),
            self.assertRaises(ExecutionError) as caught,
        ):
            execute(build_plan(self.request), self.render)
        self.assertEqual(caught.exception.outcome, "installed")
        self.assertEqual(caught.exception.phase, "promote rendered output")
        self.assertIn(f"Rendered output installed at {self.dest}", str(caught.exception))
        self.assertEqual((self.dest / "README.md").read_text(), "sample")
        self.assertEqual(list(self.dest.parent.iterdir()), [self.dest])

    def test_interrupt_after_rename_reports_installation_with_exit_130(self):
        original_replace = os.replace

        def interrupted_replace(source, destination):
            original_replace(source, destination)
            raise KeyboardInterrupt

        def run_plan(plan):
            execute(plan, self.render)

        stderr = io.StringIO()
        with (
            patch("makeghrepo_beta.execute.os.replace", interrupted_replace),
            patch("makeghrepo_beta.execute.execute", run_plan),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as caught,
        ):
            cli.main(["sample", "python", "--render", str(self.dest)])
        self.assertEqual(caught.exception.code, 130)
        self.assertIn(f"Rendered output installed at {self.dest}", stderr.getvalue())
        self.assertEqual((self.dest / "README.md").read_text(), "sample")

    def test_unknown_promotion_outcome_is_preserved_for_inspection(self):
        original_replace = os.replace
        moved = False

        def interrupted_replace(source, destination):
            nonlocal moved
            original_replace(source, destination)
            moved = True
            raise OSError("response lost after rename")

        def observation(path):
            if path == self.dest and moved:
                raise PermissionError("readback unavailable")
            return directory_state(path)

        with (
            patch("makeghrepo_beta.execute.os.replace", interrupted_replace),
            patch("makeghrepo_beta.execute.directory_state", observation),
            self.assertRaises(ExecutionError) as caught,
        ):
            execute(build_plan(self.request), self.render)
        self.assertEqual(caught.exception.outcome, "uncertain")
        self.assertIn("inspect before retrying", str(caught.exception))
        self.assertEqual((self.dest / "README.md").read_text(), "sample")

    def test_staging_root_replacement_is_not_installed_or_deleted(self):
        unrelated = self.root / "unrelated"
        unrelated.mkdir()
        (unrelated / "keep.txt").write_text("keep")

        def replaced_stage(path, data):
            path.rename(self.root / "original-stage")
            path.symlink_to(unrelated, target_is_directory=True)

        with self.assertRaises(ExecutionError) as caught:
            execute(build_plan(self.request), replaced_stage)
        self.assertEqual(caught.exception.outcome, "not installed")
        self.assertIn("Retained paths need inspection", str(caught.exception))
        self.assertFalse(self.dest.exists())
        self.assertEqual((unrelated / "keep.txt").read_text(), "keep")

    def test_cleanup_does_not_follow_symlinks_inside_owned_stage(self):
        unrelated = self.root / "unrelated"
        unrelated.mkdir()
        (unrelated / "keep.txt").write_text("keep")

        def failure(path, data):
            (path / "linked").symlink_to(unrelated, target_is_directory=True)
            raise RuntimeError("render failed")

        with self.assertRaises(ExecutionError):
            execute(build_plan(self.request), failure)
        self.assertEqual(list(self.root.iterdir()), [unrelated])
        self.assertEqual((unrelated / "keep.txt").read_text(), "keep")

    def test_lock_and_stage_creation_failures_clean_owned_parents(self):
        original_mkdir = Path.mkdir

        def mkdir(path, *args, **kwargs):
            if path.name.endswith(".lock"):
                raise PermissionError("lock creation denied")
            return original_mkdir(path, *args, **kwargs)

        for target, value, phase in (
            ("pathlib.Path.mkdir", mkdir, "acquire writer lock"),
            (
                "makeghrepo_beta.execute.tempfile.mkdtemp",
                PermissionError("staging creation denied"),
                "create staging directory",
            ),
        ):
            with self.subTest(phase=phase):
                setting = {"side_effect": value} if isinstance(value, Exception) else {"new": value}
                with patch(target, **setting), self.assertRaises(ExecutionError) as caught:
                    execute(build_plan(self.request), self.render)
                self.assertEqual(caught.exception.phase, phase)
                self.assertEqual(caught.exception.outcome, "not installed")
                self.assertEqual(list(self.root.iterdir()), [])

    def test_cleanup_failure_after_install_does_not_claim_rollback(self):
        original_rmdir = Path.rmdir

        def rmdir(path):
            if path.name.endswith(".lock"):
                raise PermissionError("lock cleanup denied")
            return original_rmdir(path)

        with patch("pathlib.Path.rmdir", rmdir), self.assertRaises(ExecutionError) as caught:
            execute(build_plan(self.request), self.render)
        self.assertEqual(caught.exception.phase, "cleanup")
        self.assertEqual(caught.exception.outcome, "installed")
        self.assertEqual(len(caught.exception.retained), 1)
        self.assertTrue(caught.exception.retained[0].name.endswith(".lock"))
        self.assertEqual((self.dest / "README.md").read_text(), "sample")

    def test_permission_preservation_failure_leaves_existing_target_untouched(self):
        self.dest.mkdir(parents=True)
        original = directory_state(self.dest)
        with (
            patch("makeghrepo_beta.execute.os.chmod", side_effect=PermissionError("mode denied")),
            self.assertRaises(ExecutionError) as caught,
        ):
            execute(build_plan(self.request), self.render)
        self.assertEqual(caught.exception.phase, "preserve destination permissions")
        self.assertEqual(directory_state(self.dest), original)
        self.assertEqual(list(self.dest.iterdir()), [])
        self.assertEqual(list(self.dest.parent.iterdir()), [self.dest])

    def test_unknown_reservation_identity_is_retained_and_named(self):
        def observation(path):
            if path == self.dest:
                raise PermissionError("reservation identity unavailable")
            return directory_state(path)

        with (
            patch("makeghrepo_beta.execute.directory_state", observation),
            self.assertRaises(ExecutionError) as caught,
        ):
            execute(build_plan(self.request), self.render)
        self.assertEqual(caught.exception.phase, "reserve destination")
        self.assertIn(self.dest, caught.exception.retained)
        self.assertEqual(list(self.dest.iterdir()), [])
        execute(build_plan(self.request), self.render)
        self.assertEqual((self.dest / "README.md").read_text(), "sample")


if __name__ == "__main__":
    unittest.main()
