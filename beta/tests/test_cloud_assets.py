"""Stdlib fixture suite; no package install, compiler, GitHub write or live repo."""

from __future__ import annotations

import base64
import copy
import csv
import hashlib
import io
import json
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from makeghrepo_beta import cloud_client as client
from makeghrepo_beta import cloud_contract as contract
from makeghrepo_beta.cloud_cli import main

ROOT = Path(__file__).resolve().parents[2]


def zip_bytes(payload):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, (content, mode) in payload.items():
            entry = zipfile.ZipInfo(name)
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(entry, content)
    return stream.getvalue()


def fixture_wheel(source):
    info = "makeghrepo_seed-0.0.0.dist-info/"
    files = {
        "makeghrepo_seed/__init__.py": (source, 0o644),
        info + "METADATA": (b"Metadata-Version: 2.3\nName: makeghrepo-seed\n"
                            b"Version: 0.0.0\nRequires-Python: >=3.14\n", 0o644),
        info + "WHEEL": (b"Wheel-Version: 1.0\nTag: py3-none-any\nRoot-Is-Purelib: true\n", 0o644),
        info + "entry_points.txt": (
            b"[console_scripts]\nmakeghrepo-seed = makeghrepo_seed:main\n", 0o644,
        ),
    }
    records = io.StringIO()
    writer = csv.writer(records, lineterminator="\n")
    for name, (content, _mode) in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        writer.writerow([name, "sha256=" + digest, str(len(content))])
    writer.writerow([info + "RECORD", "", ""])
    files[info + "RECORD"] = (records.getvalue().encode(), 0o644)
    return zip_bytes(files)


def fixture(plan):
    golden = (ROOT / "tests/golden/python.golden").read_text()
    manifest = golden.split("### FILE pyproject.toml", 1)[1].split("\n", 1)[1]
    manifest = manifest.split("\n### END", 1)[0]
    manifest = manifest.replace("quiet-otter", contract.PACKAGE)
    manifest = manifest.replace("quiet_otter", "makeghrepo_seed")
    seed = ROOT / "beta/src/makeghrepo_beta/assets/python-uv.lock.jinja"
    lock = seed.read_text().replace("[% raw %]", "").replace("[% endraw %]", "")
    lock = lock.replace("[[ project_name | to_json ]]", json.dumps(contract.PACKAGE))
    payload = {
        "source/pyproject.toml": (manifest.encode(), 0o644),
        "source/uv.lock": (lock.encode(), 0o644),
        "source/src/makeghrepo_seed/__init__.py": (b"fixture source\n", 0o644),
        "source/tests/test_smoke.py": (b"fixture test\n", 0o644),
        "source/README.md": (b"fixture readme\n", 0o644),
        contract.WHEEL: (fixture_wheel(b"fixture source\n"), 0o644),
        "build/requirements.lock": (b"fixture hash-locked build tools, never executed\n", 0o644),
    }
    result = {
        "schema": 1, "asset": plan.asset(), "asset_key": plan.asset_key,
        "run_id": 12, "run_attempt": 2,
        "checks": {check: "passed" for check in contract.CHECKS},
        "files": contract.inventory(payload),
    }
    payload["result.json"] = (contract.canonical(result), 0o644)
    archive = zip_bytes({"bundle.zip": (zip_bytes(payload), 0o644)})
    run = {
        "id": 12, "run_attempt": 2, "head_sha": plan.source_sha,
        "path": contract.WORKFLOW, "status": "completed", "conclusion": "success",
        "event": "push", "head_branch": "main",
        "repository": {"id": 34, "full_name": contract.REPOSITORY},
        "head_repository": {"id": 34, "full_name": contract.REPOSITORY},
    }
    artifact = {
        "id": 56, "name": contract.artifact_name(plan, 2), "expired": False,
        "expires_at": "2099-01-01T00:00:00Z", "size_in_bytes": len(archive),
        "digest": "sha256:" + contract.sha256(archive),
        "workflow_run": {"id": 12, "head_sha": plan.source_sha,
                         "repository_id": 34, "head_repository_id": 34},
    }
    return payload, archive, run, artifact


class API:
    def __init__(self, run, artifact, archive):
        prefix = f"repos/{contract.REPOSITORY}/actions"
        self.run_url = f"{prefix}/runs/12"
        self.responses = {
            self.run_url: contract.canonical(run),
            self.run_url + "/artifacts?per_page=100": contract.canonical({
                "total_count": 1, "artifacts": [artifact],
            }),
            f"{prefix}/artifacts/56/zip": archive,
            f"{prefix}/artifacts/56": contract.canonical(artifact),
        }
        self.calls = []
        self.after_download = lambda: None

    def get(self, path, limit=contract.MAX_JSON):
        self.calls.append(path)
        content = self.responses[path]
        if path.endswith("/zip"):
            self.after_download()
        if len(content) > limit:
            raise ValueError("fixture too large")
        return content


class CloudAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dest = Path(self.temp.name) / "daughter"
        self.plan = contract.CloudPlan("b" * 40, "someone", "quiet-otter")
        self.payload, self.archive, self.run, self.artifact = fixture(self.plan)
        self.api = API(self.run, self.artifact, self.archive)

    def test_identity_is_configuration_and_asset_key_excludes_it(self):
        other = contract.CloudPlan(self.plan.source_sha, "elsewhere", "lunar-panda")
        self.assertEqual(self.plan.asset_key, other.asset_key)
        self.assertNotEqual(self.plan.plan_id, other.plan_id)
        self.assertEqual(contract.PROFILE["package"], "makeghrepo-seed")
        self.assertEqual(
            contract.CloudPlan.parse(contract.canonical(self.plan.document())), self.plan
        )

    def test_plan_rejects_extra_fields_commands_wrong_types_and_profile_edits(self):
        variants = []
        for key, value in [("schema", True), ("operation", "exec"), ("command", "uv sync")]:
            altered = self.plan.document()
            altered[key] = value
            variants.append(altered)
        altered = self.plan.document()
        altered["asset"]["profile"]["python"] = "host"
        variants.append(altered)
        for altered in variants:
            with self.subTest(altered=altered), self.assertRaises(ValueError):
                contract.CloudPlan.parse(contract.canonical(altered))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            contract.decode(b'{"schema":1,"schema":1}')
        with self.assertRaisesRegex(ValueError, "non-finite"):
            contract.decode(b'{"schema":NaN}')

    def test_invalid_identity_and_unpinned_source_fail(self):
        for sha, owner, name in [("main", "someone", "good"), ("a"*40, "bad/name", "good"),
                                 ("a"*40, "someone", "../bad"), ("a"*40, "someone", "Bad_Name")]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                contract.CloudPlan(sha, owner, name)

    def test_plan_does_not_probe_host_or_start_subprocesses(self):
        with patch("subprocess.Popen", side_effect=AssertionError("subprocess")), \
             patch("platform.system", side_effect=AssertionError("host probe")), \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            main([
                "plan", "--source", self.plan.source_sha, "--owner", "someone", "--name", "sample"
            ])
        self.assertEqual(json.loads(output.getvalue())["asset"]["profile"], contract.PROFILE)
        self.assertFalse(self.dest.exists())

    def test_verified_install_and_retry_are_identical_without_tool_execution(self):
        with patch("subprocess.Popen", side_effect=AssertionError("unexpected tool execution")):
            self.assertEqual(
                client.consume(self.plan, 12, self.dest, self.api), "verified and installed"
            )
            inode = self.dest.stat().st_ino
            self.assertEqual(client.consume(self.plan, 12, self.dest, self.api), "already verified")
        self.assertEqual(inode, self.dest.stat().st_ino)
        self.assertEqual(
            json.loads((self.dest / "identity.json").read_text()), self.plan.identity()
        )
        self.assertEqual((self.dest / contract.WHEEL).read_bytes(), self.payload[contract.WHEEL][0])
        self.assertEqual(len(self.api.calls), 10)

    def test_existing_work_or_different_plan_is_preserved(self):
        client.consume(self.plan, 12, self.dest, self.api)
        (self.dest / "source/README.md").write_text("user work")
        with self.assertRaisesRegex(ValueError, "changed"):
            client.consume(self.plan, 12, self.dest, self.api)
        self.assertEqual((self.dest / "source/README.md").read_text(), "user work")

    def test_two_daughter_identities_share_exact_built_bytes(self):
        other = contract.CloudPlan(self.plan.source_sha, "elsewhere", "lunar-panda")
        second = self.dest.parent / "second"
        with patch("subprocess.Popen", side_effect=AssertionError("local provisioning")):
            client.consume(self.plan, 12, self.dest, self.api)
            client.consume(other, 12, second, self.api)
        for name in self.payload:
            self.assertEqual((self.dest / name).read_bytes(), (second / name).read_bytes())
        self.assertNotEqual(
            (self.dest / "identity.json").read_bytes(), (second / "identity.json").read_bytes()
        )

    def test_rejects_pr_fork_partial_failure_and_wrong_sha_before_download(self):
        variants = [("event", "pull_request"), ("status", "in_progress"),
                    ("conclusion", "failure"), ("head_sha", "c"*40), ("path", "other.yml"),
                    ("run_attempt", True), ("head_branch", "feature"),
                    ("head_repository", {"id": 99, "full_name": "other/repo"})]
        for key, value in variants:
            run = {**self.run, key: value}
            api = API(run, self.artifact, self.archive)
            with self.subTest(key=key), self.assertRaises(ValueError):
                client.consume(self.plan, 12, self.dest, api)
            self.assertEqual(len(api.calls), 1)
            self.assertFalse(self.dest.exists())

    def test_missing_expired_duplicate_or_wrong_attempt_artifact_fails(self):
        listing = {"total_count": 1, "artifacts": [self.artifact]}
        for key, value in [("expired", True), ("expires_at", "2000-01-01T00:00:00Z"),
                           ("digest", None), ("name", contract.artifact_name(self.plan, 1)),
                           ("workflow_run", {"id": 999}), ("size_in_bytes", True)]:
            bad = copy.deepcopy(listing)
            bad["artifacts"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                contract.validate_artifact(self.plan, self.run, bad)
        for bad in [{"total_count": 2, "artifacts": [self.artifact]},
                    {"total_count": 2, "artifacts": [self.artifact, self.artifact]}]:
            with self.assertRaises(ValueError):
                contract.validate_artifact(self.plan, self.run, bad)

    def test_tamper_and_failed_readback_never_install(self):
        api = API(self.run, self.artifact, self.archive[:-1] + b"x")
        with self.assertRaisesRegex(ValueError, "digest"):
            client.consume(self.plan, 12, self.dest, api)
        self.api.after_download = lambda: self.api.responses.update({
            self.api.run_url: contract.canonical({**self.run, "run_attempt": 3})
        })
        with self.assertRaisesRegex(ValueError, "attempt changed"):
            client.consume(self.plan, 12, self.dest, self.api)
        self.assertFalse(self.dest.exists())

    def test_readback_network_failure_retains_no_false_success(self):
        def fail():
            self.api.responses.pop(self.api.run_url)
        self.api.after_download = fail
        with self.assertRaises(KeyError):
            client.consume(self.plan, 12, self.dest, self.api)
        self.assertFalse(self.dest.exists())

    def test_safe_archive_rejects_traversal_links_duplicates_and_excess(self):
        for name in ["../evil", "/evil", "source/../evil", "source\\evil", "source/.git/config",
                     "source/.GIT/config", "."]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                contract.read_archive(zip_bytes({name: (b"bad", 0o644)}))
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            link = zipfile.ZipInfo("source/link")
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, "../../other")
        with self.assertRaisesRegex(ValueError, "link"):
            contract.read_archive(stream.getvalue())
        with self.assertRaisesRegex(ValueError, "duplicate"):
            contract.read_archive(zip_bytes({"source/a": (b"", 0o644), "source/A": (b"", 0o644)}))
        with patch.object(contract, "MAX_BYTES", 1), self.assertRaises(ValueError):
            contract.read_archive(self.archive)
        with self.assertRaisesRegex(ValueError, "invalid ZIP"):
            contract.read_archive(b"not a zip")

    def test_result_cannot_change_graph_or_skip_checks(self):
        for field, value in [("checks", {}), ("run_attempt", 1), ("schema", True)]:
            payload = dict(self.payload)
            result = contract.decode(payload["result.json"][0])
            result[field] = value
            payload["result.json"] = (contract.canonical(result), 0o644)
            with self.subTest(field=field), self.assertRaises(ValueError):
                contract.validate_bundle(self.plan, zip_bytes(payload), 12, 2)
        payload = dict(self.payload)
        content, mode = payload["source/uv.lock"]
        payload["source/uv.lock"] = (content.replace(b'"9.1.1"', b'"9.1.2"'), mode)
        result = contract.decode(payload.pop("result.json")[0])
        result["files"] = contract.inventory(payload)
        payload["result.json"] = (contract.canonical(result), 0o644)
        with self.assertRaisesRegex(ValueError, "packaged lock"):
            contract.validate_bundle(self.plan, zip_bytes(payload), 12, 2)

    def test_stale_writer_lock_blocks_retry_without_cleanup(self):
        local_key = contract.sha256(str(self.dest).encode())[:20]
        lock = self.dest.parent / f".makeghrepo-beta-{local_key}.lock"
        lock.mkdir()
        (lock / "previous-writer").write_text("inspect before retry")
        with self.assertRaisesRegex(RuntimeError, "another render may be active"):
            client.consume(self.plan, 12, self.dest, self.api)
        self.assertTrue((lock / "previous-writer").exists())
        self.assertFalse(self.dest.exists())

    def test_fetch_dry_run_validates_run_destination_and_reports_unverified_remote(self):
        arguments = ["fetch", "--source", self.plan.source_sha, "--owner", self.plan.owner,
                     "--name", self.plan.name, "--run", "12", "--destination", str(self.dest)]
        with patch("subprocess.Popen", side_effect=AssertionError("network")), \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            main([*arguments, "--dry-run"])
        self.assertEqual(json.loads(output.getvalue())["run_id"], 12)
        self.assertIn("unverified", json.loads(output.getvalue())["remote_state"])
        self.assertFalse(self.dest.exists())
        for bad_run in [0, -1, True]:
            with self.subTest(run=bad_run), self.assertRaises(ValueError):
                contract.prepare_fetch(self.plan, bad_run, self.dest)
        self.dest.symlink_to(Path(self.temp.name))
        with self.assertRaises(ValueError):
            contract.prepare_fetch(self.plan, 12, self.dest)

    def test_wheel_identity_source_and_record_tampering_are_rejected(self):
        wheel = contract.read_archive(self.payload[contract.WHEEL][0])
        metadata = "makeghrepo_seed-0.0.0.dist-info/"
        changes = [("makeghrepo_seed/__init__.py", b"different code"),
                   (metadata + "METADATA", b"Name: renamed\nVersion: 0.0.0\n"),
                   (metadata + "WHEEL", b"Root-Is-Purelib: false\nTag: native\n"),
                   (metadata + "RECORD", b"malformed\n")]
        for name, data in changes:
            changed = {**wheel, name: (data, 0o644)}
            with self.subTest(name=name), self.assertRaises(ValueError):
                contract.validate_wheel(zip_bytes(changed), b"fixture source\n")


if __name__ == "__main__":
    unittest.main()
