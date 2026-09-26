import os
import stat
import tomllib
from pathlib import Path

import pytest
import yaml

from makeghrepo import github, scaffold

ALL = list(scaffold.LANGUAGES)
COMBOS = [[], ["python"], ["rust", "docker"], ["swift", "python"], ["js", "css", "api"],
          ["c", "cpp", "objc", "objcpp"], ["postgres", "sql", "shell"], ALL]  # fmt: skip


def render(tmp_path: Path, languages, private=False, **extra) -> Path:
    dest = tmp_path / "quiet-otter"
    data = {
        "project_name": "quiet-otter",
        "package_name": "quiet_otter",
        "description": "a test project",
        "author_name": "Test User",
        "github_owner": "someone",
        "private": private,
        "languages": languages,
    }
    scaffold.render(dest, {**data, **extra})
    return dest


def files_of(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


BASE = {
    ".gitignore", ".editorconfig", "README.md", "LICENSE", "AGENTS.md", "CLAUDE.md", "SECURITY.md",
    ".github/dependabot.yml", ".github/pull_request_template.md", ".github/workflows/ci.yml",
    ".github/workflows/release.yml", ".github/workflows/codeql.yml",
    ".github/ISSUE_TEMPLATE/bug.yml", ".github/ISSUE_TEMPLATE/feature.yml",
    ".github/ISSUE_TEMPLATE/epic.yml", ".github/ISSUE_TEMPLATE/config.yml",
}  # fmt: skip


def test_no_language_is_just_the_base(tmp_path):
    assert files_of(render(tmp_path, [])) == BASE


@pytest.mark.parametrize(
    ("lang", "marker"),
    [("python", "pyproject.toml"), ("rust", "Cargo.toml"), ("swift", "Package.swift"),
     ("js", "eslint.config.js"), ("css", ".stylelintrc.json"), ("c", "CMakeLists.txt"),
     ("cpp", "src/main.cpp"), ("objc", "src/main.m"), ("objcpp", "src/main.mm"),
     ("api", "openapi.yaml"), ("postgres", "compose.yaml"), ("sql", ".sqlfluff"),
     ("docker", "Dockerfile"), ("shell", "scripts/hello.sh")],
)  # fmt: skip
def test_each_language_adds_only_its_files(tmp_path, lang, marker):
    got = files_of(render(tmp_path, [lang]))
    assert marker in got
    assert got >= BASE
    others = {m for lang_, m in [("rust", "Cargo.toml"), ("python", "pyproject.toml")]
              if lang_ != lang}  # fmt: skip
    assert not (others & got)


@pytest.mark.parametrize("langs", COMBOS, ids=lambda c: "+".join(c) or "none")
def test_rendered_output_is_clean(tmp_path, langs):
    dest = render(tmp_path, langs)
    for path in dest.rglob("*"):
        assert "[[" not in path.name and "[%" not in path.name and not path.name.endswith(".jinja")
        if path.is_file():
            text = path.read_text()
            assert "[[ " not in text and "[% " not in text, path
    for path in (dest / ".github").rglob("*.yml"):
        assert yaml.safe_load(path.read_text()), path


@pytest.mark.parametrize("langs", COMBOS, ids=lambda c: "+".join(c) or "none")
def test_ci_rollup_needs_every_job(tmp_path, langs):
    ci = yaml.safe_load((render(tmp_path, langs) / ".github/workflows/ci.yml").read_text())
    jobs = ci["jobs"]
    assert github.REQUIRED_CHECK in jobs
    assert set(jobs[github.REQUIRED_CHECK]["needs"]) == set(jobs) - {github.REQUIRED_CHECK}
    assert ci["permissions"] == {"contents": "read"}


def test_dependabot_follows_languages(tmp_path):
    dep = yaml.safe_load((render(tmp_path, ALL) / ".github/dependabot.yml").read_text())
    ecosystems = {u["package-ecosystem"] for u in dep["updates"]}
    assert ecosystems == {"github-actions", "uv", "cargo", "swift", "npm", "docker",
                          "docker-compose"}  # fmt: skip


def test_private_repo_has_no_codeql(tmp_path):
    assert not (
        render(tmp_path, ["python"], private=True) / ".github/workflows/codeql.yml"
    ).exists()


def test_codeql_languages(tmp_path):
    wf = yaml.safe_load((render(tmp_path, ALL) / ".github/workflows/codeql.yml").read_text())
    langs = {m["language"] for m in wf["jobs"]["analyze"]["strategy"]["matrix"]["include"]}
    assert langs == {"actions", "python", "javascript-typescript", "rust", "c-cpp", "swift"}


def test_python_and_swift_tests_dont_collide(tmp_path):
    got = files_of(render(tmp_path, ["python", "swift"]))
    assert "tests/test_smoke.py" in got
    assert any(p.startswith("SwiftTests/") for p in got)
    assert not any(p.startswith("Tests/") for p in got)


@pytest.mark.parametrize(
    "pattern", [".DS_Store", "*.sqlite", "*.sqlite3", "*.db", "*.duckdb", ".env", "target/",
                "node_modules/", ".build/", "pgdata/"],
)  # fmt: skip
def test_gitignore_covers(tmp_path, pattern):
    assert pattern in (render(tmp_path, []) / ".gitignore").read_text().splitlines()


def test_shell_script_is_executable(tmp_path):
    mode = (render(tmp_path, ["shell"]) / "scripts/hello.sh").stat().st_mode
    assert mode & stat.S_IXUSR


def test_hostile_description_stays_a_string(tmp_path):
    """Quotes/backslashes must not break out of TOML/JSON strings (e.g. to add [tool.uv])."""
    evil = 'x" \\ """; [tool.uv] index-url = "https://evil.example" é'
    dest = render(tmp_path, ["python", "rust", "js"], description=evil, author_name='A "B"')
    py = tomllib.loads((dest / "pyproject.toml").read_text())
    assert py["project"]["description"] == evil
    assert py["project"]["authors"][0]["name"] == 'A "B"'
    assert "uv" not in py["tool"]
    assert tomllib.loads((dest / "Cargo.toml").read_text())["package"]["description"] == evil
    import json

    assert json.loads((dest / "package.json").read_text())["description"] == evil


def test_refuses_non_empty_dest(tmp_path):
    (tmp_path / "quiet-otter").mkdir()
    (tmp_path / "quiet-otter" / "keep").write_text("keep me")
    with pytest.raises(FileExistsError):
        render(tmp_path, [])
    assert (tmp_path / "quiet-otter" / "keep").read_text() == "keep me"


def test_language_aliases():
    assert scaffold.language("C++") == "cpp"
    assert scaffold.language("objc++") == "objcpp"
    assert scaffold.language("pg") == "postgres"
    assert scaffold.language("quiet-otter") is None


def test_smoke_skips_missing_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(scaffold.shutil, "which", lambda _: None)
    lines = []
    scaffold.smoke_test(tmp_path, ["rust", "swift"], lines.append)
    assert all("skip" in line for line in lines) and len(lines) == 5


def test_skip_local_checks_still_makes_lockfiles(tmp_path, monkeypatch):
    """A constrained host can skip compiling/testing, but not the lockfile CI/Docker need."""
    monkeypatch.setenv("MAKEGHREPO_SKIP_LOCAL_CHECKS", "1")
    monkeypatch.setattr(scaffold.shutil, "which", lambda _: "/bin/x")  # every tool "installed"
    ran = []

    def fake_run(cmd, cwd, capture_output, text):
        ran.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(scaffold.subprocess, "run", fake_run)
    lines = []
    scaffold.smoke_test(tmp_path, ["python", "rust"], lines.append)
    assert ran == [("uv", "lock")]  # rust has no required lockfile step, so it's fully skipped
    assert any("MAKEGHREPO_SKIP_LOCAL_CHECKS" in line for line in lines)


@pytest.mark.slow
def test_python_project_passes_its_own_checks(tmp_path):
    """End-to-end: uv lock/sync, ruff, pytest, uv audit and uv build inside a new project."""
    dest = render(tmp_path, ["python"])
    scaffold.smoke_test(dest, ["python"], lambda _: None)
    assert list((dest / "dist").glob("*.whl"))


@pytest.mark.slow
@pytest.mark.skipif(os.environ.get("CI") is None and not scaffold.shutil.which("cargo"),
                    reason="needs cargo")  # fmt: skip
def test_rust_project_passes_clippy(tmp_path):
    dest = render(tmp_path, ["rust"])
    scaffold.smoke_test(dest, ["rust"], lambda _: None)


@pytest.mark.parametrize(("lang", "tool"), [("python", "uv"), ("js", "npm"), ("css", "npm")])
def test_lockfile_tools_are_required(tmp_path, monkeypatch, lang, tool):
    monkeypatch.setattr(scaffold.shutil, "which", lambda t: None if t == tool else "/bin/x")
    with pytest.raises(RuntimeError, match=tool):
        scaffold.smoke_test(tmp_path, [lang], lambda _: None)


def test_swift_manifest_has_no_trailing_commas(tmp_path):
    for langs in (["swift"], ["swift", "python"]):
        text = (render(tmp_path / "+".join(langs), langs) / "Package.swift").read_text()
        assert ",\n        )" not in text and ",\n    ]" not in text
