"""Build-hook source selection tests; real builds/installations run in beta-check."""

import importlib.util
import sys
import tempfile
import tomllib
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def hook_class(self):
        # Exercise our source-selection hook without claiming this replaces
        # Hatch integration; that separate acceptance gate builds real artifacts.
        name = "hatchling.builders.hooks.plugin.interface"
        module = types.ModuleType(name)
        module.BuildHookInterface = object
        with patch.dict(sys.modules, {name: module}):
            spec = importlib.util.spec_from_file_location("beta_hook", ROOT / "hatch_build.py")
            hook = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(hook)
        return hook.CustomBuildHook

    def test_only_beta_entrypoint_and_no_stable_dependency(self):
        beta = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        stable = tomllib.loads((ROOT.parent / "pyproject.toml").read_text())["project"]
        self.assertEqual(beta["name"], "makeghrepo-beta")
        self.assertEqual(beta["scripts"], {"makeghrepo-beta": "makeghrepo_beta:main"})
        self.assertEqual(stable["scripts"], {"makeghrepo": "makeghrepo:main"})
        self.assertFalse(any(dep.startswith("makeghrepo") for dep in beta["dependencies"]))

    def test_source_tree_and_sdist_select_the_same_payload(self):
        hook_type = self.hook_class()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            checkout = root / "checkout" / "beta"
            shared = checkout.parent / "src" / "makeghrepo"
            shared.mkdir(parents=True)
            (shared / "registry.py").write_text("registry")
            (shared / "sample.jinja").write_text("template")
            (shared / "__pycache__").mkdir()
            (shared / "__pycache__/registry.pyc").write_bytes(b"cache")
            hook = hook_type()
            hook.root = str(checkout)
            hook.target_name = "sdist"
            data = {}
            hook.initialize("standard", data)
            self.assertEqual(
                set(data["force_include"].values()),
                {"_shared/makeghrepo/registry.py", "_shared/makeghrepo/sample.jinja"},
            )
            extracted = root / "extracted"
            for source, relative in data["force_include"].items():
                target = extracted / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(Path(source).read_bytes())
            # The extracted location has no parent src directory.
            hook.root = str(extracted)
            hook.target_name = "wheel"
            wheel = {}
            hook.initialize("standard", wheel)
            self.assertEqual(
                set(wheel["force_include"].values()),
                {"makeghrepo/registry.py", "makeghrepo/sample.jinja"},
            )
            self.assertEqual(
                sorted(Path(p).read_bytes() for p in data["force_include"]),
                sorted(Path(p).read_bytes() for p in wheel["force_include"]),
            )

    def test_missing_shared_sources_fail_closed(self):
        hook = self.hook_class()()
        with tempfile.TemporaryDirectory() as temp:
            hook.root = temp
            hook.target_name = "wheel"
            with self.assertRaisesRegex(RuntimeError, "bundled makeghrepo sources"):
                hook.initialize("standard", {})


if __name__ == "__main__":
    unittest.main()
