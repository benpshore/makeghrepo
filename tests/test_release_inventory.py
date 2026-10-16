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
        workflow = yaml.safe_load((ROOT / ".github/workflows/release-build.yml").read_text())
        publication = workflow["jobs"]["publish"]["steps"][-1]["run"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = root / "bin"
            tools.mkdir()
            fake = tools / "gh"
            # The fixture records public visibility, not just successful shell
            # exit: a failed upload must retain the private draft, and retry
            # must verify the remote digest before changing that visibility.
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import json,os,sys\n"
                "from pathlib import Path\n"
                "args=sys.argv[1:]\n"
                "state=Path('state.json')\n"
                "data=json.loads(state.read_text()) if state.exists() else {'latest':'v99.0.0'}\n"
                "if args[0]=='release': assert args[2]=='v'+os.environ['VERSION']\n"
                "if args[0]=='api':\n"
                " if 'git/ref/tags' in args[1]:\n"
                "  if not data.get('tag'): sys.exit(1)\n"
                "  print(json.dumps({'object':{'type':'tag','sha':'c'*40}}))\n"
                " elif 'git/tags/' in args[1]:\n"
                "  print(json.dumps({'object':{'type':'commit','sha':data['tag_source']}}))\n"
                " elif args[1]=='--method':\n"
                "  if 'git/tags' in args[3]:\n"
                "   data['tag_source']=os.environ['SOURCE']\n"
                "   print('c'*40)\n"
                "  else:\n"
                "   assert 'sha='+('c'*40) in args\n"
                "   data['tag']=True\n"
                " elif 'releases/tags' in args[1]:\n"
                "  assets=json.loads(Path('expected-assets.json').read_text())\n"
                "  records=[dict(a,digest='sha256:'+a['sha256']) for a in assets]\n"
                "  if os.environ.get('BAD_REMOTE_DIGEST'):\n"
                "   records[0]['digest']='sha256:incorrect'\n"
                "  print(json.dumps({'assets':records}))\n"
                " else: sys.exit(2)\n"
                "elif args[:2]==['release','view']:\n"
                " if 'draft' not in data: sys.exit(1)\n"
                " print(str(data['draft']).lower())\n"
                "elif args[:2]==['release','create']: data['draft']=True\n"
                "elif args[:2]==['release','upload']:\n"
                " if os.environ.get('FAIL_UPLOAD'): sys.exit(1)\n"
                " data['uploaded']=True\n"
                "elif args[:2]==['release','edit']:\n"
                " assert data.get('uploaded')\n"
                " data['draft']=False\n"
                "else: sys.exit(2)\n"
                "state.write_text(json.dumps(data))\n"
            )
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
            env = os.environ | {
                "PATH": f"{tools}:{os.environ['PATH']}",
                "VERSION": "0.36.0",
                "SOURCE": "b" * 40,
                "GH_REPO": "fixture/no-remote",
                "GH_TOKEN": "inert-fixture",
            }
            failed = subprocess.run(
                ["bash", "-c", publication],
                cwd=root,
                env=env | {"FAIL_UPLOAD": "1"},
                capture_output=True,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertTrue(json.loads((root / "state.json").read_text())["draft"])
            corrupt = subprocess.run(
                ["bash", "-c", publication],
                cwd=root,
                env=env | {"BAD_REMOTE_DIGEST": "1"},
                capture_output=True,
            )
            self.assertNotEqual(corrupt.returncode, 0)
            self.assertTrue(json.loads((root / "state.json").read_text())["draft"])
            retry = subprocess.run(
                ["bash", "-c", publication], cwd=root, env=env, capture_output=True
            )
            self.assertEqual(retry.returncode, 0, retry.stderr.decode())
            self.assertFalse(json.loads((root / "state.json").read_text())["draft"])
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
        self.assertLess(publication.index("gh release upload"), publication.index("--draft=false"))
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
