"""Build-hook source selection tests; real builds/installations run in beta-check."""

import importlib.util
import json
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
            checkout.mkdir()
            (shared / "registry.py").write_text("registry")
            (shared / "sample.jinja").write_text("template")
            (shared / "__pycache__").mkdir()
            (shared / "__pycache__/registry.pyc").write_bytes(b"cache")
            (shared / ".env").write_text("SYNTHETIC_DO_NOT_PACKAGE=1")
            (shared / "scratch.py").write_text("untracked scratch code")
            manifest = json.dumps({"schema": 1, "files": ["registry.py", "sample.jinja"]})
            (checkout / "shared-files.json").write_text(manifest)
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
            (extracted / "shared-files.json").write_text(manifest)
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

    def test_unsafe_manifests_and_missing_files_fail_before_registering_payload(self):
        cases = [
            [],
            {"schema": 2, "files": ["registry.py"]},
            {"schema": 1, "files": ["registry.py", "registry.py"]},
            {"schema": 1, "files": ["registry.py", "../outside.py"]},
            {"schema": 1, "files": ["registry.py", "/outside.py"]},
            {"schema": 1, "files": ["registry.py", "folder\\outside.py"]},
            {"schema": 1, "files": ["registry.py", "missing.py"]},
            {"schema": 1, "files": ["registry.py", 123]},
        ]
        hook_type = self.hook_class()
        for manifest in cases:
            with self.subTest(manifest=manifest), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                shared = root / "_shared/makeghrepo"
                shared.mkdir(parents=True)
                (shared / "registry.py").write_text("registry")
                (root / "shared-files.json").write_text(json.dumps(manifest))
                hook = hook_type()
                hook.root = str(root)
                hook.target_name = "wheel"
                payload = {}
                with self.assertRaises(RuntimeError):
                    hook.initialize("standard", payload)
                self.assertEqual(payload, {})

    def test_manifest_cannot_follow_a_source_symlink(self):
        hook_type = self.hook_class()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shared = root / "_shared/makeghrepo"
            shared.mkdir(parents=True)
            (shared / "registry.py").write_text("registry")
            outside = root / "unrelated"
            outside.mkdir()
            (outside / "sample.py").write_text("do not package")
            (shared / "linked").symlink_to(outside, target_is_directory=True)
            manifest = {"schema": 1, "files": ["registry.py", "linked/sample.py"]}
            (root / "shared-files.json").write_text(json.dumps(manifest))
            hook = hook_type()
            hook.root = str(root)
            hook.target_name = "wheel"
            payload = {}
            with self.assertRaisesRegex(RuntimeError, "symlink"):
                hook.initialize("standard", payload)
            self.assertEqual(payload, {})


if __name__ == "__main__":
    unittest.main()
