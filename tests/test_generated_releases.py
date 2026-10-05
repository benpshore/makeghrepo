"""Exercise generated workflows rather than assuming Python represents all stacks."""

import tomllib

import golden_snapshots as gs
import pytest
import yaml

from makeghrepo import scaffold


@pytest.mark.parametrize("combo", sorted(gs.COMBOS))
def test_release_output_covers_public_stacks_and_preserves_private(combo, tmp_path):
    root = gs.render_combo(combo, tmp_path / "quiet-otter")
    directory = root / ".github/workflows"
    private = gs.COMBOS[combo].get("private", False)
    assert (directory / "auto-release.yml").exists() is not private
    manual = yaml.safe_load((directory / "release.yml").read_text())
    assert manual["jobs"]["publish"]["needs"] == ["plan", "build"]
    plan_commands = "\n".join(step.get("run", "") for step in manual["jobs"]["plan"]["steps"])
    assert ("check-runs?check_name=ci" in plan_commands) is not private
    build = manual["jobs"]["build"]
    assert build["permissions"] == {"contents": "read"}
    assert "GH_TOKEN" not in build.get("env", {})
    assert build["steps"][0]["with"]["persist-credentials"] is False
    publisher = manual["jobs"]["publish"]
    assert not any("checkout@" in step.get("uses", "") for step in publisher["steps"])
    assert "--draft=false" in publisher["steps"][-1]["run"]
    if not private:
        automatic = yaml.safe_load((directory / "auto-release.yml").read_text())
        assert automatic[True]["push"]["branches"] == ["main"]
        assert "tags" not in automatic[True]["push"]
        assert "pull_request" not in automatic[True]
        assert automatic["jobs"]["build"] == build
        assert automatic["jobs"]["publish"] == publisher


def test_lunar_panda_configuration_keeps_library_and_contract(tmp_path):
    root = tmp_path / "lunar-panda"
    scaffold.render(
        root,
        gs.DATA
        | {
            "project_name": "lunar-panda",
            "package_name": "lunar_panda",
            "languages": ["python", "sqlite", "api"],
            "py_lib": True,
        },
    )
    project = tomllib.loads((root / "pyproject.toml").read_text())
    assert "scripts" not in project["project"]
    assert project["project"]["dynamic"] == ["version"]
    assert (root / "sqlite/schema.sql").exists()
    contract = yaml.safe_load((root / "openapi.yaml").read_text())
    assert "/health" in contract["paths"]
    automatic = yaml.safe_load((root / ".github/workflows/auto-release.yml").read_text())
    commands = "\n".join(step.get("run", "") for step in automatic["jobs"]["build"]["steps"])
    assert "uv build" in commands
    assert "spectral-cli@6.16.3" in commands
    assert 'inventory --version "$VERSION"' in commands


def test_mixed_python_rust_requires_both_payloads(tmp_path):
    root = gs.render_combo("python+rust+docker", tmp_path / "quiet-otter")
    workflow = yaml.safe_load((root / ".github/workflows/auto-release.yml").read_text())
    commands = "\n".join(step.get("run", "") for step in workflow["jobs"]["build"]["steps"])
    assert "uv build" in commands
    assert "cargo build --release --locked" in commands
    assert workflow["jobs"]["publish"]["needs"] == ["plan", "build"]
    assert "if" not in workflow["jobs"]["publish"]
