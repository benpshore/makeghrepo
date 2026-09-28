"""The registry in src/makeghrepo/langs/ must reproduce the tables makeghrepo had before it."""

from importlib.resources import files
from pathlib import Path

import pytest

from makeghrepo import registry, scaffold

# Frozen copies of the hand-written tables the registry replaced (v0.11.0), updated in F3
# for npm --ignore-scripts and pinned npx/uvx tool versions, and extended per new token (go: E3-1, ts: E2-1).
_UV_AUDIT = ("uv", "audit", "--locked", "--preview-features", "audit-command")
_CMAKE = (("cmake", "-S", ".", "-B", "build"), ("cmake", "--build", "build"),
          ("ctest", "--test-dir", "build", "--output-on-failure"))  # fmt: skip
BEFORE = {
    "LANGUAGES": ("python", "rust", "swift", "js", "css", "c", "cpp", "objc", "objcpp",
                  "api", "postgres", "sql", "docker", "shell", "go", "ts"),
    "ALIASES": {"py": "python", "rs": "rust", "javascript": "js", "node": "js",
                "c++": "cpp", "cxx": "cpp", "objective-c": "objc", "objc++": "objcpp",
                "rest": "api", "openapi": "api", "pg": "postgres", "postgresql": "postgres",
                "sh": "shell", "bash": "shell", "golang": "go", "typescript": "ts"},
    "CHECKS": {
        "python": (("uv", "lock"), ("uv", "sync", "--locked"),
                   ("uv", "run", "ruff", "format", "--check"), ("uv", "run", "ruff", "check"),
                   ("uv", "run", "pytest", "-q"), _UV_AUDIT, ("uv", "build", "-q")),
        "rust": (("cargo", "fmt", "--check"),
                 ("cargo", "clippy", "--all-targets", "--", "-D", "warnings"),
                 ("cargo", "test", "-q")),
        "swift": (("swift", "build"), ("swift", "test")),
        "js": (("npm", "install", "--no-fund", "--ignore-scripts"), ("npm", "run", "lint"),
               ("npm", "test")),
        "css": (("npm", "install", "--no-fund", "--ignore-scripts"), ("npm", "run", "lint:css")),
        "c": _CMAKE, "cpp": _CMAKE, "objc": _CMAKE, "objcpp": _CMAKE,
        "api": (("npx", "--yes", "@stoplight/spectral-cli@6.16.3", "lint", "openapi.yaml",
                 "--fail-severity=warn"),),
        "sql": (("uvx", "sqlfluff==4.3.0", "lint", "sql"),),
        "docker": (("hadolint", "Dockerfile"),),
        "shell": (("shellcheck", "scripts/hello.sh"),),
        "go": (("go", "vet", "./..."), ("go", "test", "./..."), ("go", "build", "./...")),
        "ts": (("npm", "install", "--no-fund", "--ignore-scripts"), ("npm", "run", "typecheck"),
               ("npm", "run", "test:ts")),
    },
    "REQUIRED_TOOLS": {"python": "uv", "js": "npm", "css": "npm", "ts": "npm"},
    "SETUP_LEN": {"python": 2, "js": 1, "css": 1, "ts": 1},
}  # fmt: skip


@pytest.mark.parametrize("table", sorted(BEFORE))
def test_derived_tables_equal_the_originals(table):
    assert getattr(scaffold, table) == BEFORE[table]


def test_language_order_is_stable():
    assert list(scaffold.LANGUAGES) == [lang.id for lang in scaffold.LANGS.values()]
    assert [lang.order for lang in scaffold.LANGS.values()] == list(range(1, 17))


def _copy_langs(tmp_path: Path) -> Path:
    dest = tmp_path / "langs"
    dest.mkdir()
    for entry in (files("makeghrepo") / "langs").iterdir():
        if entry.name.endswith(".toml"):
            (dest / entry.name).write_text(entry.read_text(encoding="utf-8"))
    return dest


EXAMPLE = """schema = 1
id = "example"
order = 99
aliases = ["ex"]
kind = "language"
flag = "example"
groups = []
marker = "example.txt"
checks = [["example-tool", "check"]]
setup_len = 0
required_tool = false
ci_job = "example"
dependabot = { ecosystem = "example-eco", order = 99 }
codeql = {}
docker_priority = 0
requires = []
host_os = []
probe = []
leading_letter = false
apps = []
lockfile = ""
release = ""
npm_scripts = {}
npm_dev = {}
"""


def test_adding_a_language_is_just_adding_its_file(tmp_path):
    langs_dir = _copy_langs(tmp_path)
    (langs_dir / "example.toml").write_text(EXAMPLE)
    langs = registry.load(langs_dir)
    assert list(langs)[-1] == "example"
    assert langs["example"].checks == (("example-tool", "check"),)
    data = registry.derived(langs, ["python", "example"])
    assert data["ci_jobs"] == ["python", "example"]
    assert data["dependabot_ecosystems"] == ["github-actions", "uv", "example-eco"]


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda t: t.replace("schema = 1", "schema = 2"), "schema 2"),
        (lambda t: t.replace('marker = "example.txt"\n', ""), "missing"),
        (lambda t: t + 'surprise = "x"\n', "unknown"),
        (lambda t: t.replace('id = "example"', 'id = "other"'), "doesn't match"),
        (lambda t: t.replace("setup_len = 0", "setup_len = 5"), "setup_len"),
        (lambda t: t.replace('aliases = ["ex"]', 'aliases = ["py"]'), "already an id or alias"),
        (lambda t: t.replace("order = 99", "order = 1"), "share an order"),
    ],
)
def test_invalid_entries_are_rejected(tmp_path, edit, message):
    langs_dir = _copy_langs(tmp_path)
    (langs_dir / "example.toml").write_text(edit(EXAMPLE))
    with pytest.raises(ValueError, match=message):
        registry.load(langs_dir)


def test_derived_orders_shared_entries_once():
    data = registry.derived(scaffold.LANGS, ["css", "objc", "js", "c", "cpp"])
    assert data["ci_jobs"] == ["js", "css", "cmake"]
    assert data["dependabot_ecosystems"] == ["github-actions", "npm"]
    assert [e["language"] for e in data["codeql_entries"]] == ["javascript-typescript", "c-cpp"]
    assert list(data["npm_scripts"]) == ["lint", "test", "lint:css"]


def test_packaged_langs_dir_has_only_registry_files():
    names = [e.name for e in (files("makeghrepo") / "langs").iterdir()]
    assert sorted(names) == sorted(f"{lang}.toml" for lang in scaffold.LANGUAGES)


def test_every_registry_flag_and_group_is_a_copier_flag():
    """copier.yml still declares each derived boolean; a new token must add its line there
    (the Rust port derives the same flags from the registry, so both must agree)."""
    copier_yml = (files("makeghrepo") / "templates" / "project" / "copier.yml").read_text()
    declared = {
        line.split(":", 1)[0] for line in copier_yml.splitlines() if ": {type: bool" in line
    }
    wanted = {lang.flag for lang in scaffold.LANGS.values()}
    wanted |= {group for lang in scaffold.LANGS.values() for group in lang.groups}
    assert wanted <= declared, sorted(wanted - declared)
