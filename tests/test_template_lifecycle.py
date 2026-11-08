"""Real Copier copy/update and local-only patch-tag contracts."""

import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import copier
import pytest
from copier.errors import UnsafeTemplateError
from jinja2 import UndefinedError

from makeghrepo import registry, scaffold

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "src/makeghrepo/templates/project"
BUMP = TEMPLATE / "template/.makeghrepo/bump_version.py"
DATA = {
    "project_name": "tiny-sdk",
    "package_name": "tiny_sdk",
    "github_owner": "someone",
    "languages": ["rust", "shell", "python", "sqlite"],
    "private": True,
    "py_lib": True,
    "year": "2026",
}


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def initialize(root):
    root.mkdir(exist_ok=True)
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Template fixture")
    git(root, "config", "user.email", "fixture@example.invalid")


def commit(root):
    git(root, "add", ".")
    git(root, "commit", "--allow-empty", "-m", "fixture")


def template_repo(tmp_path):
    root = tmp_path / "upstream"
    root.mkdir()
    shutil.copy2(ROOT / "copier.yml", root / "copier.yml")
    shutil.copytree(TEMPLATE, root / "src/makeghrepo/templates/project")
    initialize(root)
    commit(root)
    git(root, "tag", "v1.0.0")
    return root


def test_packaged_copier_state_is_portable_and_records_the_library_stack(tmp_path):
    daughter = tmp_path / "daughter"
    scaffold.render(daughter, DATA, template_ref="v1.2.3")
    answers = json.loads((daughter / ".copier-answers.yml").read_text())
    assert answers["_src_path"] == scaffold.TEMPLATE_SOURCE
    assert answers["_commit"] == "v1.2.3"
    assert answers["languages"] == DATA["languages"]
    assert answers["py_lib"] is True
    assert answers["ci_jobs"] == registry.derived(scaffold.LANGS, DATA["languages"])["ci_jobs"]
    assert str(ROOT) not in (daughter / ".copier-answers.yml").read_text()
    assert tomllib.loads((daughter / "mise.toml").read_text())["tasks"]["bump:patch"]


def test_real_git_copier_update_preserves_local_work_and_stack_checks(tmp_path):
    upstream = template_repo(tmp_path)
    daughter = tmp_path / "daughter"
    data = {**registry.derived(scaffold.LANGS, DATA["languages"]), **DATA}
    copier.run_copy(
        str(upstream),
        daughter,
        data=data,
        vcs_ref="v1.0.0",
        defaults=True,
        unsafe=False,
        quiet=True,
    )
    initialize(daughter)
    hello = daughter / "scripts/hello.sh"
    hello.write_text(hello.read_text() + "# local applet customization\n")
    (daughter / "CUSTOM.md").write_text("keep local work\n")
    commit(daughter)
    before = (daughter / ".github/workflows/ci.yml").read_text()
    readme = upstream / "src/makeghrepo/templates/project/template/README.md.jinja"
    readme.write_text(readme.read_text() + "\nTemplate revision two.\n")
    commit(upstream)
    git(upstream, "tag", "v1.0.1")
    copier.run_update(
        daughter,
        vcs_ref="v1.0.1",
        defaults=True,
        unsafe=False,
        skip_tasks=True,
        overwrite=True,
        quiet=True,
    )
    assert "Template revision two." in (daughter / "README.md").read_text()
    assert "# local applet customization" in hello.read_text()
    assert (daughter / "CUSTOM.md").read_text() == "keep local work\n"
    assert (daughter / ".github/workflows/ci.yml").read_text() == before
    assert json.loads((daughter / ".copier-answers.yml").read_text())["_commit"] == "v1.0.1"


def test_jinja_undefined_variables_fail_instead_of_silently_disappearing(tmp_path):
    root = tmp_path / "template"
    shutil.copytree(TEMPLATE, root)
    (root / "template/README.md.jinja").write_text("[[ missing_required_template_value ]]\n")
    with pytest.raises(UndefinedError):
        copier.run_copy(
            str(root), tmp_path / "daughter", data=DATA, defaults=True, unsafe=False, quiet=True
        )


def test_template_tasks_remain_untrusted(tmp_path):
    root = tmp_path / "template"
    shutil.copytree(TEMPLATE, root)
    sentinel = tmp_path / "must-not-run"
    config = root / "copier.yml"
    config.write_text(config.read_text() + f"\n_tasks: ['touch {sentinel}']\n")
    with pytest.raises(UnsafeTemplateError):
        copier.run_copy(
            str(root), tmp_path / "daughter", data=DATA, defaults=True, unsafe=False, quiet=True
        )
    assert not sentinel.exists()


def bump(root, *args):
    return subprocess.run(
        [sys.executable, str(BUMP), *args], cwd=root, text=True, capture_output=True
    )


def test_patch_preview_is_read_only_and_apply_is_local_retryable(tmp_path):
    initialize(tmp_path)
    commit(tmp_path)
    planned = bump(tmp_path, "--dry-run")
    assert planned.returncode == 0 and "v0.0.1" in planned.stdout
    assert git(tmp_path, "tag", "--list") == ""
    applied = bump(tmp_path)
    assert applied.returncode == 0, applied.stderr
    assert git(tmp_path, "tag", "--list") == "v0.0.1"
    assert git(tmp_path, "cat-file", "-t", "v0.0.1") == "tag"
    assert bump(tmp_path).returncode == 0
    assert git(tmp_path, "tag", "--list") == "v0.0.1"
    assert git(tmp_path, "remote") == ""


def test_patch_selection_is_numeric_and_refuses_dirty_work(tmp_path):
    initialize(tmp_path)
    commit(tmp_path)
    git(tmp_path, "tag", "v1.9.9")
    git(tmp_path, "tag", "v99.0.0-rc1")
    commit(tmp_path)
    assert "v1.9.10" in bump(tmp_path, "--dry-run").stdout
    (tmp_path / "uncommitted.txt").write_text("keep me")
    failed = bump(tmp_path)
    assert failed.returncode != 0 and "clean" in failed.stderr
    assert git(tmp_path, "tag", "--list", "v1.9.10") == ""


def test_patch_collision_on_an_unrelated_branch_is_not_retagged(tmp_path):
    initialize(tmp_path)
    commit(tmp_path)
    original = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "switch", "--orphan", "other")
    (tmp_path / "different.txt").write_text("unrelated history")
    commit(tmp_path)
    git(tmp_path, "tag", "v0.0.1")
    tagged = git(tmp_path, "rev-parse", "v0.0.1")
    git(tmp_path, "checkout", original)
    failed = bump(tmp_path, "--dry-run")
    assert failed.returncode != 0 and "another commit" in failed.stderr
    assert git(tmp_path, "rev-parse", "v0.0.1") == tagged


@pytest.mark.parametrize(
    ("installed", "expected"),
    [
        ("0.53.0", "v0.53.0"),
        ("0.53.0.post1.dev0+abc123def", "abc123def"),
        ("0.54.0.dev2+gabc123def", "abc123def"),
    ],
)
def test_clean_wheel_revision(installed, expected):
    assert scaffold._revision_from_version(installed) == expected


@pytest.mark.parametrize(
    "installed", ["0.53.0+dirty", "0.53.0.post1.dev0+abc123def.dirty", "0.0.0.dev0"]
)
def test_unidentified_wheel_is_not_mislabeled(installed):
    with pytest.raises(RuntimeError, match="explicit template revision"):
        scaffold._revision_from_version(installed)
