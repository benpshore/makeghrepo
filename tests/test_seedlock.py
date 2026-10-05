import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from makeghrepo import scaffold, seedlock

LANGUAGES = [["python"], ["python", "sqlite"], ["python", "api"], ["python", "sqlite", "api"]]


def render(dest, name="lunar-panda", *, lib=True, private=False, license_choice="none", langs=None):
    scaffold.render(dest, {
        "project_name": name, "package_name": name.replace("-", "_"),
        "description": 'A "quoted" description', "author_name": 'A "quoted" author',
        "github_owner": "fixture-owner", "year": "2026", "private": private,
        "py_lib": lib, "project_license": license_choice, "languages": langs or LANGUAGES[-1],
    })  # fmt: skip
    return dest


@pytest.mark.parametrize("name", ["lunar-panda", "cedar-otter"])
@pytest.mark.parametrize(
    "lib,private,license_choice", [(True, False, "none"), (False, True, "MIT")]
)
@pytest.mark.parametrize("langs", LANGUAGES)
def test_packaged_contract_accepts_rendered_variants(
    tmp_path, monkeypatch, name, lib, private, license_choice, langs
):
    dest = render(
        tmp_path / "project",
        name,
        lib=lib,
        private=private,
        license_choice=license_choice,
        langs=langs,
    )

    def forbidden(*args, **kwargs):
        pytest.fail("data validation invoked a subprocess")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    seedlock.validate_bootstrap(dest, langs, name, lib=lib)


@pytest.mark.parametrize(
    "mutation",
    [
        "dependency",
        "source",
        "build",
        "entry-point",
        "python",
        "lock-hash",
        "lock-version",
        "lock-source",
        "lock-root",
        "lock-extra",
        "missing-lock",
        "symlink-lock",
        "unknown-tool",
        "optional-dependency",
        "build-hook",
    ],
)
def test_changed_bootstrap_data_is_refused_without_execution(tmp_path, monkeypatch, mutation):
    dest = render(tmp_path / "project")
    manifest = dest / "pyproject.toml"
    lock = dest / "uv.lock"
    if mutation == "missing-lock":
        lock.unlink()
    elif mutation == "symlink-lock":
        other = tmp_path / "copy.lock"
        lock.rename(other)
        lock.symlink_to(other)
    elif mutation.startswith("lock-"):
        contents = lock.read_text()
        replacements = {
            "lock-hash": ("sha256:", "sha256:0"),
            "lock-version": ('version = "0.4.6"', 'version = "0.4.5"'),
            "lock-source": ("https://pypi.org/simple", "https://example.invalid/simple"),
            "lock-root": ('name = "lunar-panda"', 'name = "different-project"'),
        }
        if mutation == "lock-extra":
            contents += '\n[[package]]\nname = "extra"\nversion = "1"\n'
        else:
            old, new = replacements[mutation]
            assert old in contents
            contents = contents.replace(old, new, 1)
        lock.write_text(contents)
    else:
        contents = manifest.read_text()
        replacements = {
            "dependency": ("dependencies = []", 'dependencies = ["requests"]'),
            "build": ('"hatchling.build"', '"other.build"'),
            "python": ('">=3.14"', '">=3.12"'),
        }
        additions = {
            "source": '\n[tool.uv.sources]\npytest = {path = "../elsewhere"}\n',
            "entry-point": '\n[project.scripts]\nextra = "extra:main"\n',
            "unknown-tool": "\n[tool.unknown]\noption = true\n",
            "optional-dependency": '\n[project.optional-dependencies]\nextra = ["requests"]\n',
            "build-hook": '\n[tool.hatch.build.hooks.custom]\npath = "hook.py"\n',
        }
        if mutation in additions:
            contents += additions[mutation]
        else:
            old, new = replacements[mutation]
            assert old in contents
            contents = contents.replace(old, new, 1)
        manifest.write_text(contents)

    def forbidden(*args, **kwargs):
        pytest.fail("validation executed a subprocess")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with pytest.raises(ValueError, match="Cannot bootstrap"):
        seedlock.validate_bootstrap(dest, LANGUAGES[-1], "lunar-panda", lib=True)


@pytest.mark.parametrize("langs", [["js"], ["rust"], ["python", "docker"], ["sqlite"]])
def test_uncovered_stacks_are_explicit(langs):
    with pytest.raises(ValueError, match="another lock plan"):
        seedlock.require_supported(langs)


def test_project_name_collision_is_refused(tmp_path):
    dest = render(tmp_path / "project", "pytest")
    with pytest.raises(ValueError, match="Cannot bootstrap"):
        seedlock.validate_bootstrap(dest, LANGUAGES[-1], "pytest", lib=True)


def test_recorded_seed_template_digest_matches_packaged_bytes():
    import hashlib

    source = Path(scaffold.__file__).parent
    contract = json.loads((source / "data/python-seed.json").read_text())
    template = source / "templates/project/template/[% if py %]uv.lock[% endif %].jinja"
    assert (
        hashlib.sha256(template.read_bytes()).hexdigest()
        == contract["provenance"]["template_sha256"]
    )
    assert contract["schema"] == 1
    assert (
        tomllib.loads(
            template.read_text()
            .replace("[% raw %]", "")
            .replace("[% endraw %]", "")
            .replace("[[ project_name | to_json ]]", '"makeghrepo-seed"')
        )["requires-python"]
        == ">=3.14"
    )


@pytest.mark.slow
@pytest.mark.parametrize("name", ["lunar-panda", "cedar-otter"])
@pytest.mark.parametrize("lib", [False, True])
@pytest.mark.parametrize("langs", LANGUAGES)
def test_uv_accepts_seed_offline_with_empty_cache(tmp_path, name, lib, langs):
    """Real uv compatibility runs in trusted CI, never from the installed CLI."""
    dest = render(tmp_path / "project", name, lib=lib, langs=langs)
    before = (dest / "uv.lock").read_bytes()
    uv = shutil.which("uv")
    assert uv is not None
    result = subprocess.run(
        [uv, "lock", "--check", "--offline", "--no-config", "--python", sys.executable],
        cwd=dest,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path / "home"),
            "UV_CACHE_DIR": str(tmp_path / "cache"),
            "UV_NO_ENV_FILE": "1",
            "UV_PYTHON_DOWNLOADS": "never",
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (dest / "uv.lock").read_bytes() == before
    assert not (dest / ".venv").exists()


@pytest.mark.slow
def test_maintenance_generator_emits_reviewable_candidates_without_replacing_package(tmp_path):
    source = Path(scaffold.__file__).resolve().parents[2]
    template = (
        source / "src/makeghrepo/templates/project/template/[% if py %]uv.lock[% endif %].jinja"
    )
    packaged_contract = source / "src/makeghrepo/data/python-seed.json"
    before = (template.read_bytes(), packaged_contract.read_bytes())
    output = tmp_path / "candidate"
    result = subprocess.run(
        [sys.executable, source / "scripts/regen-python-lock", "--out", str(output)],
        cwd=source,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    candidate = json.loads((output / "python-seed.json").read_text())
    assert candidate["schema"] == 1
    case_names = {case["name"] for case in candidate["provenance"]["cases"]}
    assert case_names == {"lunar-panda", "cedar-otter"}
    assert candidate["manifest"] == json.loads(before[1])["manifest"]
    assert (output / "python-uv.lock.jinja").is_file()
    assert (template.read_bytes(), packaged_contract.read_bytes()) == before
