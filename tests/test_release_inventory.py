"""Release fixtures use tiny inert packages, never real repositories or tags."""

import hashlib
import io
import json
import runpy
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
record = runpy.run_path(str(ROOT / "scripts/release-inventory"))["record"]


class ReleaseInventoryTests(unittest.TestCase):
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
            checksum.write_text(f"{hashlib.sha256(binary.read_bytes()).hexdigest()}  {binary.name}\n")
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
            self.assertEqual(data["jobs"]["release"]["uses"], "./.github/workflows/release-build.yml")


if __name__ == "__main__":
    unittest.main()
