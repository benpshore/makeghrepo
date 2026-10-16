"""Release fixtures use tiny inert packages, never real repositories or tags."""

import hashlib
import io
import json
import os
import runpy
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

import yaml
from release_fixtures import verify_transfer, write_fixtures

ROOT = Path(__file__).resolve().parents[1]
record = runpy.run_path(str(ROOT / "scripts/release-inventory"))["record"]


def publication_fixture(root):
    workflow = yaml.safe_load((ROOT / ".github/workflows/release-build.yml").read_text())
    publication = workflow["jobs"]["publish"]["steps"][-1]["run"]
    tools = root / "bin"
    tools.mkdir()
    fake = tools / "gh"
    fake.write_text("#!/usr/bin/env python3\n" + (ROOT / "tests/fake_release_api.py").read_text())
    fake.chmod(0o755)
    (root / "payload").mkdir()
    content = b"verified inert payload"
    (root / "payload" / "fixture.whl").write_bytes(content)
    (root / "expected-assets.json").write_text(
        json.dumps(
            [
                {
                    "name": "fixture.whl",
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            ]
        )
    )
    (root / "state.json").write_text(
        json.dumps(
            {
                "latest": None,
                "objects": {},
                "tags": {},
                "releases": {},
                "tag_creations": 0,
                "release_creations": 0,
                "publications": 0,
                "uploads": 0,
            }
        )
    )
    env = os.environ | {
        "PATH": f"{tools}:{os.environ['PATH']}",
        "VERSION": "0.36.0",
        "SOURCE": "b" * 40,
        "GH_REPO": "fixture/no-remote",
        "GH_TOKEN": "inert-fixture",
    }
    return publication, env


def seed_release(root, tag, *, draft=False, prerelease=False, created_at="2026-01-01T00:00:00Z"):
    """Create prior server state independently of the workflow under test."""
    state = root / "state.json"
    data = json.loads(state.read_text())
    oid = hashlib.sha1(tag.encode(), usedforsecurity=False).hexdigest()
    data["objects"][oid] = {"tag": tag, "source": "b" * 40}
    data["tags"][tag] = oid
    expected = json.loads((root / "expected-assets.json").read_text())
    data["releases"][tag] = {
        "id": len(data["releases"]) + 1,
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "created_at": created_at,
        "assets": [dict(a, digest="sha256:" + a["sha256"]) for a in expected],
    }
    if not draft and not prerelease:
        data["latest"] = tag
    state.write_text(json.dumps(data))


class ReleaseInventoryTests(unittest.TestCase):
    def test_incomplete_or_corrupt_transfer_fails_before_publication(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/release-build.yml").read_text())
        verification = workflow["jobs"]["publish"]["steps"][-2]["run"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "dist"
            dist.mkdir()
            version, source = "0.36.0", "a" * 40
            write_fixtures(dist, version, source)
            self.assertEqual(verify_transfer(dist, version, source), 10)
            env = os.environ | {"VERSION": version, "SOURCE": source}
            missing = dist / "makeghrepo-aarch64-apple-darwin"
            content = missing.read_bytes()
            missing.unlink()
            result = subprocess.run(
                ["bash", "-c", verification], cwd=root, env=env, capture_output=True
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "payload").exists())
            missing.write_bytes(b"corrupt payload")
            result = subprocess.run(
                ["bash", "-c", verification], cwd=root, env=env, capture_output=True
            )
            self.assertNotEqual(result.returncode, 0)
            missing.write_bytes(content)
            result = subprocess.run(
                ["bash", "-c", verification], cwd=root, env=env, capture_output=True
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(len(list((root / "payload").iterdir())), 8)

    def test_failed_upload_never_publishes_and_draft_retry_completes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publication, env = publication_fixture(root)
            failed = subprocess.run(
                ["bash", "-c", publication],
                cwd=root,
                env=env | {"FAIL_UPLOAD": "1"},
                capture_output=True,
            )
            self.assertNotEqual(failed.returncode, 0)
            state = json.loads((root / "state.json").read_text())
            self.assertTrue(state["releases"]["v0.36.0"]["draft"])
            self.assertIsNone(state["latest"])
            # A newer version is published while the old failed draft waits.
            seed_release(root, "v0.37.0")
            corrupt = subprocess.run(
                ["bash", "-c", publication],
                cwd=root,
                env=env | {"BAD_REMOTE_DIGEST": "1"},
                capture_output=True,
            )
            self.assertNotEqual(corrupt.returncode, 0)
            state = json.loads((root / "state.json").read_text())
            self.assertTrue(state["releases"]["v0.36.0"]["draft"])
            self.assertEqual(state["latest"], "v0.37.0")
            retry = subprocess.run(
                ["bash", "-c", publication], cwd=root, env=env, capture_output=True
            )
            self.assertEqual(retry.returncode, 0, retry.stderr.decode())
            state = json.loads((root / "state.json").read_text())
            self.assertFalse(state["releases"]["v0.36.0"]["draft"])
            self.assertEqual(state["latest"], "v0.37.0")
            self.assertEqual(state["tag_creations"], 1)
            self.assertEqual(state["release_creations"], 1)
            self.assertEqual(state["publications"], 1)
            published = (root / "state.json").read_text()
            public_retry = subprocess.run(
                ["bash", "-c", publication], cwd=root, env=env, capture_output=True
            )
            self.assertEqual(public_retry.returncode, 0, public_retry.stderr.decode())
            self.assertEqual((root / "state.json").read_text(), published)
            collision = subprocess.run(
                ["bash", "-c", publication],
                cwd=root,
                env=env | {"SOURCE": "d" * 40},
                capture_output=True,
            )
            self.assertNotEqual(collision.returncode, 0)
            self.assertEqual((root / "state.json").read_text(), published)

    def test_latest_is_numeric_and_independent_of_publication_order(self):
        # The shared queue serializes both callers, but its arrival order need
        # not be version order. Recovered 0.9 must not replace published 0.10.
        for versions in [("0.9.0", "0.10.0"), ("0.10.0", "0.9.0")]:
            with self.subTest(versions=versions), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                publication, env = publication_fixture(root)
                seed_release(root, "v99.0.0", draft=True)
                seed_release(root, "v100.0.0", prerelease=True)
                latest = None
                for version in versions:
                    result = subprocess.run(
                        ["bash", "-c", publication],
                        cwd=root,
                        env=env | {"VERSION": version},
                        capture_output=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr.decode())
                    latest = max(
                        filter(None, [latest, version]),
                        key=lambda v: tuple(map(int, v.split("."))),
                    )
                    state = json.loads((root / "state.json").read_text())
                    self.assertEqual(state["latest"], "v" + latest)
                self.assertEqual(state["latest"], "v0.10.0")
                self.assertEqual(state["tag_creations"], 2)
                self.assertEqual(state["release_creations"], 2)

    def test_lost_publication_response_retry_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publication, env = publication_fixture(root)
            seed_release(root, "v0.35.0")
            result = subprocess.run(
                ["bash", "-c", publication],
                cwd=root,
                env=env | {"LOSE_PUBLICATION_RESPONSE": "1"},
                capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            state = json.loads((root / "state.json").read_text())
            self.assertFalse(state["releases"]["v0.36.0"]["draft"])
            self.assertEqual(state["latest"], "v0.36.0")
            self.assertEqual(state["publications"], 1)
            published = (root / "state.json").read_text()
            retry = subprocess.run(
                ["bash", "-c", publication], cwd=root, env=env, capture_output=True
            )
            self.assertEqual(retry.returncode, 0, retry.stderr.decode())
            self.assertEqual((root / "state.json").read_text(), published)
            self.assertEqual(state["tag_creations"], 1)
            self.assertEqual(state["release_creations"], 1)

    def test_external_publication_outside_queue_can_race_latest_decision(self):
        # Preserve the earlier interleaving as an explicit boundary. This
        # external writer bypasses the callers' shared queue. The Releases API
        # has no documented conditional PATCH, so numeric ordering is not
        # guaranteed for that unsupported concurrent writer.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publication, env = publication_fixture(root)
            seed_release(root, "v0.35.0")
            seed_release(root, "v0.37.0", draft=True)
            result = subprocess.run(
                ["bash", "-c", publication],
                cwd=root,
                env=env | {"PUBLISH_BEFORE_PATCH": "v0.37.0"},
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            state = json.loads((root / "state.json").read_text())
            self.assertFalse(state["releases"]["v0.37.0"]["draft"])
            self.assertEqual(state["latest"], "v0.36.0")
            self.assertEqual(state["publications"], 2)

    def test_draft_lookup_uses_validated_numeric_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publication, env = publication_fixture(root)
            seed_release(root, "v0.36.0", draft=True)
            unsupported = subprocess.run(
                ["gh", "api", "repos/fixture/no-remote/releases/tags/v0.36.0"],
                cwd=root,
                env=env,
                capture_output=True,
            )
            self.assertNotEqual(unsupported.returncode, 0)
            self.assertIn(b"HTTP 404", unsupported.stderr)
            (root / "requests.jsonl").unlink()
            result = subprocess.run(
                ["bash", "-c", publication], cwd=root, env=env, capture_output=True
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            requests = [
                json.loads(line) for line in (root / "requests.jsonl").read_text().splitlines()
            ]
            self.assertIn(["release", "view", "v0.36.0", "--json", "databaseId,tagName"], requests)
            self.assertIn(["api", "repos/fixture/no-remote/releases/1"], requests)
            self.assertFalse(any("/releases/tags/" in str(request) for request in requests))
            state = json.loads((root / "state.json").read_text())
            self.assertEqual(state["tag_creations"], 0)
            self.assertEqual(state["release_creations"], 0)
            self.assertFalse(state["releases"]["v0.36.0"]["draft"])

    def test_latest_ignores_creation_date_and_reads_every_catalog_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publication, env = publication_fixture(root)
            # The numeric maximum is on page two, with an older creation date.
            for patch in range(100):
                seed_release(root, f"v0.1.{patch}", created_at="2026-02-01T00:00:00Z")
            seed_release(root, "v0.37.0", created_at="2025-01-01T00:00:00Z")
            seed_release(root, "v0.36.0", draft=True, created_at="2026-03-01T00:00:00Z")
            result = subprocess.run(
                ["bash", "-c", publication], cwd=root, env=env, capture_output=True
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            pages = json.loads((root / "release-pages.json").read_text())
            self.assertEqual([len(page) for page in pages], [100, 2])
            self.assertEqual(pages[-1][-1]["tag_name"], "v0.37.0")
            state = json.loads((root / "state.json").read_text())
            self.assertEqual(state["latest"], "v0.37.0")
            self.assertFalse(state["releases"]["v0.36.0"]["draft"])

    def test_malformed_or_incomplete_publication_evidence_fails_closed(self):
        cases = [
            {"BAD_CATALOG": value}
            for value in (
                "duplicate_id",
                "duplicate_tag",
                "malformed_id",
                "malformed_state",
                "malformed_tag",
                "missing_candidate",
                "malformed_pages",
            )
        ] + [
            {name: "1"}
            for name in (
                "FAIL_CATALOG",
                "BAD_LOOKUP_ID",
                "BAD_LOOKUP_TAG",
                "BAD_RELEASE_ID",
                "BAD_RELEASE_TAG",
            )
        ]
        for errors in cases:
            with self.subTest(errors=errors), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                publication, env = publication_fixture(root)
                seed_release(root, "v0.35.0")
                result = subprocess.run(
                    ["bash", "-c", publication],
                    cwd=root,
                    env=env | errors,
                    capture_output=True,
                )
                self.assertNotEqual(result.returncode, 0)
                state = json.loads((root / "state.json").read_text())
                self.assertEqual(state["latest"], "v0.35.0")
                self.assertTrue(state["releases"]["v0.36.0"]["draft"])
                self.assertEqual(state["publications"], 0)

    def test_python_metadata_mismatch_fails_before_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with zipfile.ZipFile(root / "makeghrepo-0.1.0-py3-none-any.whl", "w") as wheel:
                wheel.writestr(
                    "makeghrepo-0.1.0.dist-info/METADATA",
                    "Name: makeghrepo\nVersion: 0.1.0\n",
                )
            with tarfile.open(root / "makeghrepo-0.1.0.tar.gz", "w:gz") as sdist:
                content = b"Name: makeghrepo\nVersion: 0.2.0\n"
                info = tarfile.TarInfo("makeghrepo-0.1.0/PKG-INFO")
                info.size = len(content)
                sdist.addfile(info, io.BytesIO(content))
            with self.assertRaisesRegex(ValueError, "metadata"):
                record("python", root, "0.1.0", "a" * 40)
            self.assertFalse((root / "python.json").exists())

    def test_native_checksum_and_source_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            slot = "aarch64-apple-darwin"
            binary = root / f"makeghrepo-{slot}"
            binary.write_bytes(b"inert native fixture")
            checksum = root / f"{binary.name}.sha256"
            checksum.write_text("incorrect checksum\n")
            with self.assertRaisesRegex(ValueError, "checksum"):
                record(slot, root, "0.36.0", "b" * 40)
            checksum.write_text(
                f"{hashlib.sha256(binary.read_bytes()).hexdigest()}  {binary.name}\n"
            )
            record(slot, root, "0.36.0", "b" * 40)
            inventory = json.loads((root / f"{slot}.json").read_text())
            self.assertEqual(inventory["source"], "b" * 40)
            self.assertEqual(inventory["version"], "0.36.0")
            self.assertEqual(len(inventory["assets"]), 2)

    def test_every_build_blocks_publisher_and_write_jobs_never_build(self):
        directory = ROOT / ".github/workflows"
        callers = {
            path.name
            for path in directory.glob("*.yml")
            if any(
                job.get("uses") == "./.github/workflows/release-build.yml"
                for job in yaml.safe_load(path.read_text()).get("jobs", {}).values()
            )
        }
        self.assertEqual(callers, {"auto-release.yml", "release.yml"})
        workflow = yaml.safe_load((ROOT / ".github/workflows/release-build.yml").read_text())
        jobs = workflow["jobs"]
        self.assertEqual(jobs["publish"]["needs"], ["python", "binaries"])
        self.assertNotIn("if", jobs["publish"])
        self.assertEqual(len(jobs["binaries"]["strategy"]["matrix"]["include"]), 3)
        for name in ("python", "binaries"):
            self.assertEqual(jobs[name]["permissions"], {"contents": "read"})
            self.assertNotIn("GH_TOKEN", jobs[name].get("env", {}))
            checkout = jobs[name]["steps"][0]
            self.assertFalse(checkout["with"]["persist-credentials"])
            self.assertEqual(checkout["with"]["ref"], "${{ inputs.sha }}")
        steps = jobs["publish"]["steps"]
        self.assertFalse(any("checkout@" in step.get("uses", "") for step in steps))
        self.assertNotIn("GH_TOKEN", jobs["publish"]["env"])
        publication = steps[-1]["run"]
        self.assertLess(publication.index("gh release upload"), publication.index("-F draft=false"))
        self.assertIn("--draft --verify-tag", publication)
        self.assertIn('test "$sha" = "$SOURCE"', publication)
        for entry in ("auto-release.yml", "release.yml"):
            data = yaml.safe_load((ROOT / ".github/workflows" / entry).read_text())
            self.assertEqual(
                data["jobs"]["release"]["uses"], "./.github/workflows/release-build.yml"
            )
            self.assertEqual(
                data["concurrency"],
                {"group": "auto-release", "cancel-in-progress": False, "queue": "max"},
            )
            plan = data["jobs"]["plan"]
            self.assertEqual(plan["permissions"], {"contents": "read", "checks": "read"})
            self.assertNotIn("GH_TOKEN", plan.get("env", {}))
            wait, checkout = plan["steps"][:2]
            self.assertEqual(wait["env"]["SHA"], "${{ github.sha }}")
            self.assertEqual(wait["env"]["GH_TOKEN"], "${{ github.token }}")
            self.assertIn('.app.slug == "github-actions"', wait["run"])
            self.assertIn("commits/$SHA/check-runs?check_name=ci", wait["run"])
            self.assertEqual(checkout["with"]["ref"], "${{ github.sha }}")
            self.assertFalse(checkout["with"]["persist-credentials"])
            self.assertTrue(all("GH_TOKEN" not in s.get("env", {}) for s in plan["steps"][1:]))
        automatic = yaml.safe_load((ROOT / ".github/workflows/auto-release.yml").read_text())
        self.assertEqual(automatic[True]["push"], {"branches": ["main"]})
        self.assertNotIn("pull_request", automatic[True])


if __name__ == "__main__":
    unittest.main()
