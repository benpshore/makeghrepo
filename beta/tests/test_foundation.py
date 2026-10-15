"""Dependency-free planner/recovery tests, including adversarial effect boundaries.

Run: PYTHONPATH=beta/src:src python -m unittest discover -s beta/tests
The installed distribution/real renderer is checked separately in CI.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from makeghrepo_beta import cli
from makeghrepo_beta.execute import ExecutionError, execute
from makeghrepo_beta.plan import LANGS, Request, build_plan


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.dest = self.root / "new" / "preview"

    def request(self, **kwargs):
        return replace(Request(render=self.dest, words=("sample", "python")), **kwargs)

    def render(self, path, data):
        (path / "README.md").write_text(data["project_name"])

    def test_plan_canonicalizes_aliases_and_deduplicates_without_effects(self):
        plan = build_plan(self.request(words=("My Project", "py", "python", "c++")))
        self.assertEqual(plan.name, "my-project")
        self.assertEqual(plan.languages, ("python", "cpp"))
        self.assertEqual(list(self.root.iterdir()), [])
        data = json.loads(plan.as_json())
        self.assertEqual(data["operation"], "offline-render")
        self.assertEqual(data["inputs"], plan.template_data())
        self.assertFalse(data["destination_exists"])

    def test_language_first_uses_same_fixed_offline_default(self):
        plan = build_plan(self.request(words=("python",)))
        self.assertEqual(plan.name, "quiet-otter")
        self.assertEqual(plan.languages, ("python",))

    def test_invalid_inputs_fail_before_any_writes(self):
        cases = (
            {"words": ("..",)},
            {"words": ("",)},
            {"words": ("../repo",)},
            {"words": ("repo\\path",)},
            {"words": ("7up", "rust")},
            {"words": ("sample", "cobol")},
            {"words": (), "lib": True},
            {"owner": "../other"},
            {"owner": "a" * 40},
            {"license": "arbitrary"},
            {"year": "2\n26"},
            {"year": "\uff12\uff10\uff12\uff16"},  # Fullwidth digits are intentionally invalid.
            {"author": "Test\x1b"},
            {"description": "text\x00"},
            {"render": None},
        )
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_plan(self.request(**changes))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_existing_nonempty_destination_is_preserved(self):
        self.dest.mkdir(parents=True)
        original = self.dest / "keep.txt"
        original.write_text("keep")
        with self.assertRaisesRegex(ValueError, "not empty"):
            build_plan(self.request())
        self.assertEqual(original.read_text(), "keep")

    def test_symlinks_and_file_ancestors_are_refused(self):
        target = self.root / "target"
        target.mkdir()
        (self.root / "new").symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            build_plan(self.request())
        self.assertEqual(list(target.iterdir()), [])
        (self.root / "new").unlink()
        (self.root / "new").write_text("keep")
        with self.assertRaises(ValueError):
            build_plan(self.request())
        (self.root / "new").unlink()
        self.dest.parent.mkdir()
        self.dest.symlink_to(self.root / "missing")
        with self.assertRaises(ValueError):
            build_plan(self.request())

    def test_execute_consumes_the_exact_planned_inputs(self):
        request = self.request(private=True, lib=True, license="MIT", year="2030")
        plan = build_plan(request)
        seen = []

        def render(path, data):
            seen.append(data)
            self.assertFalse(self.dest.exists())
            self.render(path, data)

        execute(plan, render)
        self.assertEqual(seen, [plan.template_data()])
        self.assertEqual((self.dest / "README.md").read_text(), "sample")
        self.assertEqual(list(self.dest.parent.iterdir()), [self.dest])
        self.assertFalse((self.dest / ".git").exists())

    def test_empty_existing_target_is_replaced_only_after_complete_render(self):
        self.dest.mkdir(parents=True, mode=0o750)
        original_mode = self.dest.stat().st_mode & 0o777
        plan = build_plan(self.request())

        def render(path, data):
            self.assertEqual(list(self.dest.iterdir()), [])
            self.render(path, data)

        execute(plan, render)
        self.assertEqual(self.dest.stat().st_mode & 0o777, original_mode)
        self.assertEqual(list(self.dest.parent.iterdir()), [self.dest])

    def test_render_failure_rolls_back_only_owned_paths(self):
        sentinel = self.root / "keep.txt"
        sentinel.write_text("keep")

        def broken(path, data):
            self.render(path, data)
            raise RuntimeError("injected template failure")

        with self.assertRaisesRegex(RuntimeError, "injected template failure"):
            execute(build_plan(self.request()), broken)
        self.assertEqual(list(self.root.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_text(), "keep")
        execute(build_plan(self.request()), self.render)
        self.assertTrue((self.dest / "README.md").exists())

    def test_interrupt_also_removes_owned_staging(self):
        def interrupt(path, data):
            self.render(path, data)
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            execute(build_plan(self.request()), interrupt)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_failed_promotion_preserves_existing_empty_target(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    self.dest.mkdir(parents=True)
                with (
                    patch("makeghrepo_beta.execute.os.replace", side_effect=OSError("injected")),
                    self.assertRaisesRegex(ExecutionError, "injected"),
                ):
                    execute(build_plan(self.request()), self.render)
                self.assertEqual(self.dest.exists(), existing)
                if existing:
                    self.assertEqual(list(self.dest.iterdir()), [])
                    self.assertEqual(list(self.dest.parent.iterdir()), [self.dest])
                else:
                    self.assertEqual(list(self.root.iterdir()), [])

    def test_destination_changed_since_plan_is_preserved(self):
        plan = build_plan(self.request())
        self.dest.mkdir(parents=True)
        (self.dest / "keep.txt").write_text("other writer")
        with self.assertRaisesRegex(ValueError, "not empty"):
            execute(plan, self.render)
        self.assertEqual((self.dest / "keep.txt").read_text(), "other writer")

    def test_concurrent_writer_cannot_replace_destination(self):
        plan = build_plan(self.request())

        def render(path, data):
            with self.assertRaisesRegex(RuntimeError, "another render may be active"):
                execute(plan, self.render)
            self.render(path, data)

        execute(plan, render)
        self.assertEqual((self.dest / "README.md").read_text(), "sample")
        self.assertEqual(list(self.dest.parent.iterdir()), [self.dest])

    def test_uncooperative_writer_during_render_is_preserved(self):
        self.dest.parent.mkdir(parents=True)

        def render(path, data):
            self.render(path, data)
            self.dest.mkdir()
            (self.dest / "other.txt").write_text("keep")

        with self.assertRaisesRegex(ExecutionError, "not empty"):
            execute(build_plan(self.request()), render)
        self.assertEqual((self.dest / "other.txt").read_text(), "keep")
        self.assertEqual(list(self.dest.parent.iterdir()), [self.dest])

    def test_cleanup_failure_reports_retained_path_and_preserves_destination(self):
        def broken(path, data):
            self.render(path, data)
            raise RuntimeError("injected render failure")

        with (
            patch("makeghrepo_beta.execute.shutil.rmtree", side_effect=OSError("denied")),
            self.assertRaisesRegex(RuntimeError, "Retained paths need inspection") as caught,
        ):
            execute(build_plan(self.request()), broken)
        self.assertIn(".makeghrepo-beta-stage-", str(caught.exception))
        self.assertIn("injected render failure", str(caught.exception))
        self.assertFalse(self.dest.exists())

    def test_cli_and_executor_receive_the_same_plan(self):
        args = ["sample", "python", "-p", "--lib", "--render", str(self.dest)]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cli.main([*args, "--dry-run", "--json"])
        planned = json.loads(output.getvalue())
        with (
            patch("makeghrepo_beta.execute.execute") as effect,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            cli.main(args)
        self.assertEqual(json.loads(effect.call_args.args[0].as_json()), planned)
        self.assertFalse(self.dest.exists())

    def test_help_lists_actual_flags_and_languages(self):
        help_text = cli.parser().format_help()
        for token in ("--dry-run", "--render", "--owner", "--json", "-p", *LANGS):
            self.assertIn(token, help_text)

    def test_cloud_project_name_retains_positional_and_options_first_forms(self):
        for args in (
            ["cloud", "python", "--render", str(self.dest)],
            ["--render", str(self.dest), "cloud", "python"],
            ["cloud", "--render", str(self.dest), "python"],
            ["--owner", "cloud", "--render", str(self.dest), "cloud", "python"],
        ):
            with self.subTest(args=args):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    cli.main([*args, "--dry-run", "--json"])
                planned = json.loads(output.getvalue())
                with (
                    patch("makeghrepo_beta.execute.execute") as effect,
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    cli.main(args)
                self.assertEqual(effect.call_args.args[0].name, "cloud")
                self.assertEqual(effect.call_args.args[0].languages, ("python",))
                self.assertEqual(json.loads(effect.call_args.args[0].as_json()), planned)
        self.assertFalse(self.dest.exists())

    def test_only_recognized_cloud_subcommands_are_dispatched(self):
        for operation in ("plan", "fetch"):
            args = [operation, "--source", "b" * 40, "--owner", "someone", "--name", "sample"]
            with self.subTest(operation=operation), patch("makeghrepo_beta.cloud_cli.main") as cloud:
                cli.main(["cloud", *args])
                cloud.assert_called_once_with(args)

    def test_cli_json_without_dry_run_fails_before_writes(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            cli.main(["sample", "--render", str(self.dest), "--json"])
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(self.dest.exists())

    def test_help_version_and_dry_run_need_no_effect_capable_imports(self):
        # Fresh processes prevent an earlier import hiding a subprocess side effect.
        code = """
import builtins
import os
import subprocess
import sys
sys.dont_write_bytecode = True
original_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name in {'copier', 'git', 'makeghrepo.scaffold', 'makeghrepo_beta.execute'}:
        raise AssertionError('effect-capable import: ' + name)
    return original_import(name, *args, **kwargs)
def forbidden(*args, **kwargs):
    raise AssertionError('external command or filesystem mutation')
builtins.__import__ = guarded
subprocess.Popen = forbidden
os.mkdir = forbidden
os.remove = forbidden
os.rename = forbidden
os.replace = forbidden
def audit(event, args):
    if event == 'open' and args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
        raise AssertionError('file write during read-only command')
    if event in {'os.symlink', 'os.chmod', 'os.rmdir', 'socket.connect'}:
        raise AssertionError('mutation/network during read-only command: ' + event)
sys.addaudithook(audit)
from makeghrepo_beta import main
main()
"""
        cases = [
            ["--help"],
            ["--version"],
            ["sample", "python", "--render", str(self.dest), "--dry-run", "--json"],
        ]
        for args in cases:
            with self.subTest(args=args):
                result = subprocess.run(
                    [sys.executable, "-B", "-c", code, *args],
                    env={**os.environ, "PATH": ""},
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=15,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
