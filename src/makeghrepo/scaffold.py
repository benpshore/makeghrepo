"""Render the copier template and smoke-test the generated project."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from datetime import date
from importlib.resources import as_file, files
from pathlib import Path

import copier

LANGUAGES = (
    "python", "rust", "swift", "js", "css", "c", "cpp", "objc", "objcpp",
    "api", "postgres", "sql", "docker", "shell",
)  # fmt: skip
ALIASES = {
    "py": "python", "rs": "rust", "javascript": "js", "node": "js",
    "c++": "cpp", "cxx": "cpp", "objective-c": "objc", "objc++": "objcpp",
    "rest": "api", "openapi": "api", "pg": "postgres", "postgresql": "postgres",
    "sh": "shell", "bash": "shell",
}  # fmt: skip

UV_AUDIT = ("uv", "audit", "--locked", "--preview-features", "audit-command")
CMAKE = (("cmake", "-S", ".", "-B", "build"), ("cmake", "--build", "build"),
         ("ctest", "--test-dir", "build", "--output-on-failure"))  # fmt: skip

# Local checks per language. Each is skipped if its tool isn't installed (CI still runs it).
# postgres has none: its CI job applies the migrations to a real database.
CHECKS: dict[str, tuple[tuple[str, ...], ...]] = {
    "python": (
        ("uv", "lock"), ("uv", "sync", "--locked"), ("uv", "run", "ruff", "format", "--check"),
        ("uv", "run", "ruff", "check"), ("uv", "run", "pytest", "-q"), UV_AUDIT,
        ("uv", "build", "-q"),
    ),
    "rust": (
        ("cargo", "fmt", "--check"),
        ("cargo", "clippy", "--all-targets", "--", "-D", "warnings"),
        ("cargo", "test", "-q"),
    ),
    "swift": (("swift", "build"), ("swift", "test")),
    "js": (("npm", "install", "--no-fund"), ("npm", "run", "lint"), ("npm", "test")),
    "css": (("npm", "install", "--no-fund"), ("npm", "run", "lint:css")),
    "c": CMAKE, "cpp": CMAKE, "objc": CMAKE, "objcpp": CMAKE,
    "api": (("npx", "--yes", "@stoplight/spectral-cli", "lint", "openapi.yaml",
             "--fail-severity=warn"),),
    "sql": (("uvx", "sqlfluff", "lint", "sql"),),
    "docker": (("hadolint", "Dockerfile"),),
    "shell": (("shellcheck", "scripts/hello.sh"),),
}  # fmt: skip


# These create lockfiles that CI (`--locked`, `npm ci`) and the Dockerfiles depend on.
# Each is its language's first CHECKS command, so it can't drift out of sync.
REQUIRED_TOOLS = {lang: CHECKS[lang][0][0] for lang in ("python", "js", "css")}


def language(word: str) -> str | None:
    word = word.lower()
    return word if word in LANGUAGES else ALIASES.get(word)


def render(dest: Path, data: dict[str, object]) -> None:
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"{dest} already exists and is not empty")
    with as_file(files("makeghrepo") / "templates" / "project") as src:
        copier.run_copy(
            str(src),
            str(dest),
            data={"year": str(date.today().year), **data},
            defaults=True,
            unsafe=False,
            quiet=True,
            overwrite=False,
        )


def smoke_test(dest: Path, languages: list[str], log: Callable[[str], None]) -> None:
    """Run each chosen language's lint/test/build locally. Raises on the first failure."""
    commands = list(dict.fromkeys(cmd for lang in languages for cmd in CHECKS.get(lang, ())))
    if "postgres" in languages and "sql" in languages:
        commands = [(*c, "db") if c[:2] == ("uvx", "sqlfluff") else c for c in commands]
    for lang, tool in REQUIRED_TOOLS.items():
        if lang in languages and shutil.which(tool) is None:
            raise RuntimeError(f"{lang} needs {tool} installed locally to create its lockfile")
    for cmd in commands:
        if shutil.which(cmd[0]) is None:
            log(f"  - skip {' '.join(cmd)} ({cmd[0]} not installed; CI will run it)")
            continue
        log("  $ " + " ".join(cmd))
        result = subprocess.run(cmd, cwd=dest, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"failed: {' '.join(cmd)}\n{result.stdout}{result.stderr}")
