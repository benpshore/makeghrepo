import tomllib
from pathlib import Path

import pytest
import yaml

from makeghrepo import github, scaffold

DATA = {
    "project_name": "quiet-otter",
    "package_name": "quiet_otter",
    "description": "a test project",
    "author_name": "Test User",
    "author_email": "test@example.com",
    "github_owner": "someone",
    "python_version": "3.14",
}


@pytest.fixture
def rendered(tmp_path) -> Path:
    dest = tmp_path / "quiet-otter"
    scaffold.render("python", dest, DATA)
    return dest


def test_expected_files(rendered):
    for rel in [
        "pyproject.toml",
        "README.md",
        "LICENSE",
        "AGENTS.md",
        "CLAUDE.md",
        "SECURITY.md",
        ".gitignore",
        ".python-version",
        ".editorconfig",
        "src/quiet_otter/__init__.py",
        "tests/test_smoke.py",
        ".github/workflows/ci.yml",
        ".github/workflows/codeql.yml",
        ".github/dependabot.yml",
        ".github/pull_request_template.md",
        ".github/ISSUE_TEMPLATE/bug.yml",
        ".github/ISSUE_TEMPLATE/feature.yml",
        ".github/ISSUE_TEMPLATE/epic.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
    ]:
        assert (rendered / rel).is_file(), rel
    # no CODEOWNERS: it would auto-request (and notify) the owner on every PR
    assert not (rendered / ".github/CODEOWNERS").exists()


def test_no_unrendered_jinja(rendered):
    for path in rendered.rglob("*"):
        assert not path.name.endswith(".jinja"), path
        assert "{{" not in path.name, path
        if path.is_file() and ".github" not in path.parts:
            assert "{{" not in path.read_text(), path


def test_pyproject(rendered):
    meta = tomllib.loads((rendered / "pyproject.toml").read_text())
    assert meta["project"]["name"] == "quiet-otter"
    assert meta["project"]["requires-python"] == ">=3.14"
    assert meta["project"]["scripts"]["quiet-otter"] == "quiet_otter:main"
    assert (rendered / ".python-version").read_text().strip() == "3.14"


@pytest.mark.parametrize(
    "pattern", [".DS_Store", "*.sqlite", "*.sqlite3", "*.db", "*.duckdb", ".env", ".venv/"]
)
def test_gitignore_covers(rendered, pattern):
    assert pattern in (rendered / ".gitignore").read_text().splitlines()


def test_github_yaml_is_valid(rendered):
    for path in (rendered / ".github").rglob("*.yml"):
        assert yaml.safe_load(path.read_text()), path


def test_ci_job_name_matches_required_check(rendered):
    ci = yaml.safe_load((rendered / ".github/workflows/ci.yml").read_text())
    names = {job.get("name", key) for key, job in ci["jobs"].items()}
    assert github.REQUIRED_CHECK in names


def test_workflows_have_least_privilege(rendered):
    for path in (rendered / ".github/workflows").glob("*.yml"):
        wf = yaml.safe_load(path.read_text())
        assert wf["permissions"] == {"contents": "read"}, path


def test_dependabot_ecosystems(rendered):
    dep = yaml.safe_load((rendered / ".github/dependabot.yml").read_text())
    assert {u["package-ecosystem"] for u in dep["updates"]} == {"uv", "github-actions"}
    # every label dependabot applies must be created by configure_labels
    created = {name for name, _, _ in github.LABELS}
    for update in dep["updates"]:
        assert set(update["labels"]) <= created


def test_refuses_non_empty_dest(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "file").write_text("keep me")
    with pytest.raises(FileExistsError):
        scaffold.render("python", tmp_path / "x", DATA)
    assert (tmp_path / "x" / "file").read_text() == "keep me"


def test_unknown_template(tmp_path):
    with pytest.raises(ValueError):
        scaffold.render("cobol", tmp_path / "x", DATA)


@pytest.mark.slow
def test_generated_project_passes_its_own_checks(rendered):
    """End-to-end: uv lock, sync, ruff format/check and pytest inside the new project."""
    scaffold.smoke_test(rendered, lambda _: None)
    assert (rendered / "uv.lock").is_file()
