"""Real process-exit boundaries and untrusted recovery-record inspection."""

import contextlib
import io
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from makeghrepo_beta import cli
from makeghrepo_beta.execute import ExecutionError, execute
from makeghrepo_beta.journal import Journal, inspect_records, prefix
from makeghrepo_beta.plan import Request, build_plan

CRASH_CHILD = r'''
import os
import sys
from pathlib import Path
from makeghrepo_beta.execute import execute
from makeghrepo_beta.journal import Journal
from makeghrepo_beta.plan import Request, build_plan

dest, boundary = Path(sys.argv[1]), sys.argv[2]
mkdir, replace, rmdir, write = Path.mkdir, os.replace, Path.rmdir, Journal.write

def stop():
    os._exit(77)

def make(path, *args, **kwargs):
    mkdir(path, *args, **kwargs)
    if (
        (boundary == "parent" and path == dest.parent)
        or (boundary == "lock" and path.name.endswith(".lock"))
        or (boundary == "stage" and path.name.startswith(".makeghrepo-beta-stage-"))
        or (boundary == "reservation" and path == dest)
    ):
        stop()

def move(source, target):
    if target == dest and boundary == "before-promotion":
        stop()
    if str(source).endswith(".pending") and boundary == "journal-pending":
        stop()
    replace(source, target)
    if target == dest and boundary == "after-promotion":
        stop()

def remove(path):
    if path.name.endswith(".lock") and boundary == "before-cleanup":
        stop()
    rmdir(path)
    if path.name.endswith(".lock") and boundary == "after-cleanup":
        stop()

def record(self, **kwargs):
    write(self, **kwargs)
    if kwargs.get("initial") and boundary == "initial-record":
        stop()

def render(path, data):
    (path / "README.md").write_text("complete output")
    if boundary == "render":
        stop()

Path.mkdir, Path.rmdir, os.replace, Journal.write = make, remove, move, record
execute(build_plan(Request(words=("sample",), render=dest)), render)
'''


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dest = self.root / "new" / "preview"

    @staticmethod
    def render(path, data):
        (path / "README.md").write_text(data["project_name"])

    def plan(self):
        return build_plan(Request(words=("sample",), render=self.dest))

    def test_success_record_is_private_and_observed_without_writes(self):
        record = execute(self.plan(), self.render)
        self.assertEqual(stat.S_IMODE(record.stat().st_mode), 0o600)
        data = json.loads(record.read_text())
        self.assertEqual(data["outcome"], "installed")
        self.assertEqual(len(data["plan_sha256"]), 64)
        before = {
            p: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in self.root.rglob("*")
            if p.is_file()
        }
        with patch("subprocess.Popen", side_effect=AssertionError("process")):
            report = inspect_records(self.dest)
        after = {
            p: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in self.root.rglob("*")
            if p.is_file()
        }
        self.assertEqual(before, after)
        self.assertEqual(report["records"][0]["observation"], "destination_matches_staged_identity")
        for event in data["events"]:
            if event["status"] == "confirmed":
                previous = data["events"][:data["events"].index(event)]
                self.assertTrue(
                    any(
                        e["path"] == event["path"]
                        and e["operation"] == event["operation"]
                        and e["status"] == "attempted"
                        for e in previous
                    )
                )

    def test_process_exit_at_effect_boundaries_retains_inspectable_evidence(self):
        boundaries = (
            "initial-record",
            "journal-pending",
            "parent",
            "lock",
            "stage",
            "render",
            "reservation",
            "before-promotion",
            "after-promotion",
            "before-cleanup",
            "after-cleanup",
        )
        for boundary in boundaries:
            with self.subTest(boundary=boundary):
                root = self.root / boundary
                root.mkdir()
                dest = root / "new" / "preview"
                result = subprocess.run(
                    [sys.executable, "-B", "-c", CRASH_CHILD, str(dest), boundary],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                self.assertEqual(result.returncode, 77, result.stdout + result.stderr)
                before = {p: p.stat().st_mtime_ns for p in root.rglob("*")}
                report = inspect_records(dest)
                self.assertEqual(before, {p: p.stat().st_mtime_ns for p in root.rglob("*")})
                self.assertTrue(report["records"])
                self.assertFalse(
                    any(
                        r["observation"] == "invalid_or_unreadable_record"
                        for r in report["records"]
                    )
                )
                if boundary in {"after-promotion", "before-cleanup", "after-cleanup"}:
                    self.assertEqual((dest / "README.md").read_text(), "complete output")
                    self.assertTrue(
                        any(
                            r["observation"] == "destination_matches_staged_identity"
                            for r in report["records"]
                        )
                    )
                else:
                    self.assertFalse((dest / "README.md").exists())
                recorded = {
                    e["path"]
                    for p in root.glob(prefix(dest) + "*")
                    for e in json.loads(p.read_text())["events"]
                }
                for path in root.rglob("*"):
                    if path.is_dir():
                        self.assertIn(str(path), recorded)

    def test_journal_failure_before_project_effects_is_reported(self):
        with (
            patch("os.fsync", side_effect=OSError("disk unavailable")),
            self.assertRaisesRegex(RuntimeError, "Recovery record write failed"),
        ):
            execute(self.plan(), self.render)
        self.assertFalse(self.dest.parent.exists())
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def test_failed_atomic_update_preserves_previous_snapshot_and_pending_file(self):
        journal = Journal(self.plan())
        journal.write(initial=True)
        before = journal.path.read_bytes()
        with (
            patch("makeghrepo_beta.journal.os.replace", side_effect=OSError("rename failed")),
            self.assertRaises(RuntimeError),
        ):
            journal.event("create_parent", self.dest.parent, "attempted")
        self.assertEqual(journal.path.read_bytes(), before)
        self.assertTrue(journal.path.with_suffix(".json.pending").exists())
        self.assertEqual(len(inspect_records(self.dest)["records"]), 2)
        self.assertFalse(self.dest.parent.exists())

    def test_failed_final_record_does_not_hide_installed_output(self):
        with (
            patch.object(Journal, "finish", side_effect=OSError("record failed")),
            self.assertRaises(ExecutionError) as caught,
        ):
            execute(self.plan(), self.render)
        self.assertEqual(caught.exception.outcome, "installed")
        self.assertIn("Recovery record:", str(caught.exception))
        self.assertEqual((self.dest / "README.md").read_text(), "sample")

    def test_truncated_oversized_and_foreign_records_are_untrusted(self):
        record = execute(self.plan(), self.render)
        original = record.read_bytes()
        outside = self.root / "outside"
        outside.write_text("keep")
        malformed = json.loads(original)
        malformed["events"].append(
            {"operation": "cleanup", "path": str(outside), "status": "attempted"}
        )
        for raw in (b"{", b"x" * 262_145, b"[]", json.dumps(malformed).encode()):
            with self.subTest(size=len(raw)):
                record.write_bytes(raw)
                report = inspect_records(self.dest)
                self.assertEqual(
                    report["records"][0]["observation"], "invalid_or_unreadable_record"
                )
                self.assertEqual(outside.read_text(), "keep")
                self.assertEqual(record.read_bytes(), raw)
        record.write_bytes(original)

    def test_symlink_record_and_ancestor_are_never_followed(self):
        record = execute(self.plan(), self.render)
        record.unlink()
        target = self.root / "private-content"
        target.write_text("never expose this content")
        record.symlink_to(target)
        report = inspect_records(self.dest)
        self.assertEqual(report["records"][0]["observation"], "invalid_or_unreadable_record")
        self.assertNotIn("never expose", json.dumps(report))
        link = self.root / "linked"
        link.symlink_to(self.dest.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            inspect_records(link / self.dest.name)

    def test_status_cli_is_read_only_and_does_not_replay_unknown_data(self):
        execute(self.plan(), self.render)
        output = io.StringIO()
        with (
            patch("os.mkdir", side_effect=AssertionError("mkdir")),
            patch("os.replace", side_effect=AssertionError("rename")),
            patch("os.unlink", side_effect=AssertionError("unlink")),
            patch("subprocess.Popen", side_effect=AssertionError("process")),
            contextlib.redirect_stdout(output),
        ):
            cli.main(["--status", str(self.dest), "--json"])
        self.assertEqual(json.loads(output.getvalue())["operation"], "recovery-inspection")

    def test_planned_record_location_is_used_and_changes_fail_before_writes(self):
        root = self.root / "planned"
        root.mkdir()
        dest = root / "new" / "preview"
        plan = build_plan(Request(words=("sample",), render=dest))
        self.assertEqual(json.loads(plan.as_json())["recovery_directory"], str(root))
        root.rename(self.root / "previous")
        root.mkdir()
        with self.assertRaisesRegex(ValueError, "changed after planning"):
            execute(plan, self.render)
        self.assertEqual(list(root.iterdir()), [])

    def test_initial_record_interrupt_names_the_retained_record(self):
        with (
            patch("os.fsync", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt) as caught,
        ):
            execute(self.plan(), self.render)
        (record,) = self.root.iterdir()
        self.assertIn(str(record), str(caught.exception))
        self.assertFalse(self.dest.parent.exists())

    def test_moved_installed_output_is_reported_as_changed_not_restored(self):
        execute(self.plan(), self.render)
        saved = self.dest.with_name("saved")
        self.dest.rename(saved)
        self.dest.mkdir()
        (self.dest / "unrelated.txt").write_text("keep")
        report = inspect_records(self.dest)
        self.assertEqual(report["records"][0]["recorded_outcome"], "installed")
        self.assertEqual(report["records"][0]["observation"], "destination_changed_or_unknown")
        self.assertEqual((self.dest / "unrelated.txt").read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
