"""Fixture execution of the producer's stage ordering, with all tools replaced."""

import copy
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from makeghrepo_beta import cloud_build as builder
from makeghrepo_beta import cloud_contract as contract

from test_cloud_assets import fixture


class CloudBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = contract.CloudPlan("b" * 40, "someone", "quiet-otter")
        self.payload, *_ = fixture(self.plan)
        self.commands = []
        self.fail_stage = None
        self.stage_count = 0

    def render(self, destination, data):
        self.assertEqual(data["project_name"], contract.PACKAGE)
        self.assertEqual(data["languages"], ["python"])
        for name, (content, _mode) in self.payload.items():
            if name.startswith("source/"):
                path = destination / name.removeprefix("source/")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)

    def command(self, command, cwd, env):
        self.commands.append(list(command))
        self.stage_count += 1
        if self.stage_count == self.fail_stage:
            raise RuntimeError("induced cloud stage failure")
        self.assertNotIn("GH_TOKEN", env)
        self.assertNotIn("UV_NO_VERIFY_HASHES", env)
        if command == ["uv", "--version"]:
            return "uv 0.12.23 (fixture)"
        if command[:3] == ["uv", "pip", "compile"]:
            path = Path(command[command.index("--output-file") + 1])
            path.write_bytes(self.payload["build/requirements.lock"][0])
        if command[:2] == ["uv", "build"]:
            path = Path(command[command.index("--out-dir") + 1])
            path.mkdir()
            (path / Path(contract.WHEEL).name).write_bytes(self.payload[contract.WHEEL][0])
        if command[0].endswith("/installed/bin/makeghrepo-seed"):
            return "Hello from makeghrepo-seed!"
        return ""

    def execute(self, destination):
        module = types.ModuleType("makeghrepo.scaffold")
        module.render = self.render
        with (
            patch.dict(sys.modules, {"makeghrepo.scaffold": module}),
            patch("platform.system", return_value="Linux"),
            patch("platform.machine", return_value="x86_64"),
            patch("platform.python_version", return_value="3.14.8"),
            patch.object(builder, "run", side_effect=self.command),
            patch.dict(
                os.environ, {"GH_TOKEN": "synthetic-never-store", "UV_NO_VERIFY_HASHES": "1"}
            ),
        ):
            builder.build(self.plan, destination, 12, 2)

    def test_producer_emits_bundle_only_after_every_stage_and_revalidates_it(self):
        destination = self.root / "complete"
        self.execute(destination)
        contents = contract.validate_bundle(
            self.plan, (destination / "bundle.zip").read_bytes(), 12, 2
        )
        self.assertEqual(contents[contract.WHEEL], self.payload[contract.WHEEL])
        build = next(c for c in self.commands if c[:2] == ["uv", "build"])
        self.assertIn("--offline", build)
        self.assertIn("--no-build-isolation", build)
        sync = next(c for c in self.commands if c[:3] == ["uv", "pip", "sync"])
        self.assertIn("--require-hashes", sync)
        self.assertEqual(self.commands[-1][0].split("/")[-1], contract.PACKAGE)

    def test_failure_at_each_stage_cannot_emit_a_successful_result(self):
        self.execute(self.root / "reference")
        count = self.stage_count
        for index in range(1, count + 1):
            self.stage_count = 0
            self.fail_stage = index
            destination = self.root / f"failed-{index}"
            with self.subTest(stage=index), self.assertRaisesRegex(RuntimeError, "induced"):
                self.execute(destination)
            self.assertFalse(destination.exists())

    def test_seed_provenance_and_source_builder_requirements_are_explicit(self):
        self.assertEqual(builder.BUILD_REQUIREMENTS, tuple(contract.PROFILE["build_requirements"]))
        self.assertIn(b'name = "makeghrepo-seed"', builder.packaged_lock())
        baseline = copy.deepcopy(contract.PROFILE)
        altered = self.plan.asset()
        altered["profile"]["build_requirements"].append("unreviewed")
        self.assertEqual(contract.PROFILE, baseline)

    def test_wrong_toolchain_fails_before_render_or_subprocess(self):
        with (
            patch("platform.python_version", return_value="3.13.5"),
            patch.object(builder, "run", side_effect=AssertionError("tool execution")),
        ):
            with self.assertRaisesRegex(ValueError, "toolchain"):
                builder.build(self.plan, self.root / "wrong", 12, 2)


if __name__ == "__main__":
    unittest.main()
