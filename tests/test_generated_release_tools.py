"""Version fixtures for the shared helper shipped by Python and Rust."""

import json
import os
import runpy
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = runpy.run_path(
    str(ROOT / "src/makeghrepo/templates/project/template/.github/release-tools.py.jinja")
)


class GeneratedReleaseToolsTests(unittest.TestCase):
    def test_stable_minor_lookup_and_source_retry(self):
        select = TOOLS["select"]
        self.assertEqual(select([], []), "0.1.0")
        self.assertEqual(select(["v0.9.0", "v0.10.0", "v8.0.0-rc1"], []), "0.11.0")
        self.assertEqual(select(["v0.1.0", "v0.10.0"], ["v0.1.0"]), "0.1.0")

    def test_mixed_cargo_npm_and_api_versions_are_materialized_together(self):
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            os.chdir(directory)
            try:
                cargo = '[package]\nname = "fixture"\nversion = "0.1.0"\n\n[dependencies]\n'
                Path("Cargo.toml").write_text(cargo)
                Path("package.json").write_text('{"name":"fixture","version":"0.1.0"}')
                Path("package-lock.json").write_text(
                    '{"version":"0.1.0","packages":{"":{"version":"0.1.0"},'
                    '"node_modules/example":{"version":"9.0.0"}}}'
                )
                Path("openapi.yaml").write_text(
                    "openapi: 3.1.0\ninfo:\n  title: fixture\n  version: 0.1.0\n"
                    "paths:\n  /health: {}\n"
                )
                manifests = TOOLS["prepare"]("0.2.0")
                self.assertEqual(set(manifests.values()), {"0.2.0"})
                self.assertIn('version = "0.2.0"', Path("Cargo.toml").read_text())
                lock = json.loads(Path("package-lock.json").read_text())
                self.assertEqual(lock["packages"][""]["version"], "0.2.0")
                self.assertEqual(lock["packages"]["node_modules/example"]["version"], "9.0.0")
                contract = Path("openapi.yaml").read_text()
                self.assertIn("  version: 0.2.0\n", contract)
                self.assertIn("paths:\n  /health: {}", contract)
            finally:
                os.chdir(previous)

    def test_component_only_inventory_has_version_and_source(self):
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            os.chdir(directory)
            try:
                Path("dist").mkdir()
                Path("dist/release-manifests.json").write_text(
                    json.dumps({"version": "0.2.0", "source": "a" * 40, "materialized": {}})
                )
                TOOLS["inventory"]("0.2.0", "a" * 40)
                data = json.loads(Path("dist/inventory.json").read_text())
                self.assertEqual(data["version"], "0.2.0")
                self.assertEqual(data["source"], "a" * 40)
                self.assertEqual(
                    {asset["name"] for asset in data["assets"]},
                    {"release-manifests.json", "SHA256SUMS"},
                )
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
