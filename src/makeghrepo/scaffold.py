"""Render the copier template and smoke-test the generated project."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
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

# How many of each language's leading CHECKS commands must run sequentially,
# in that order, before the rest of that language's checks are safe to start:
# `uv sync`/`npm install` have to finish before anything reads the environment
# they build. Everything after that prefix is independent of the others (and
# of every other requested language) and can run concurrently.
SETUP_LEN = {"python": 2, "js": 1, "css": 1}

# Every command in these languages' CHECKS forms one dependency chain end to
# end (configure-then-build-then-test, or a shared build cache/target dir that
# concurrent invocations of the same tool would fight over), so the whole
# tuple runs as a single sequential unit instead of being split into a
# setup/pool prefix like python/js/css are.
CHAIN_LANGS = {"rust", "swift", "c", "cpp", "objc", "objcpp"}


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
    """Run each chosen language's lint/test/build locally. Raises on the first failure.

    A tool being installed doesn't mean it's cheap to run here: a constrained host
    (no cooling, a minimal CI runner) may have e.g. cargo installed but still want
    to skip compiling. Set ``MAKEGHREPO_SKIP_LOCAL_CHECKS`` to skip every check
    except the lockfile step CI and the Dockerfiles depend on (still required).

    Setup (lockfile/install) commands run first and in order, since everything
    else assumes they finished. What's left runs concurrently: each language's
    remaining checks are independent of every other language's, and languages
    in ``CHAIN_LANGS`` contribute their whole tuple as one sequential unit so a
    shared build cache never sees two invocations of the same tool at once.
    """
    skip = bool(os.environ.get("MAKEGHREPO_SKIP_LOCAL_CHECKS"))
    for lang, tool in REQUIRED_TOOLS.items():
        if lang in languages and shutil.which(tool) is None:
            raise RuntimeError(f"{lang} needs {tool} installed locally to create its lockfile")
    lockfile_cmds = {CHECKS[lang][0] for lang in REQUIRED_TOOLS}

    seen: set[tuple[str, ...]] = set()
    setup: list[tuple[str, ...]] = []
    units: list[tuple[tuple[str, ...], ...]] = []  # each is one sequential unit to pool
    for lang in languages:
        cmds = CHECKS.get(lang, ())
        if lang == "sql" and "postgres" in languages:
            cmds = tuple((*c, "db") if c[:2] == ("uvx", "sqlfluff") else c for c in cmds)
        if lang in CHAIN_LANGS:
            chain = tuple(c for c in cmds if c not in seen)
            seen.update(chain)
            if chain:
                units.append(chain)
            continue
        for i, cmd in enumerate(cmds):
            if cmd in seen:
                continue
            seen.add(cmd)
            if i < SETUP_LEN.get(lang, 0):
                setup.append(cmd)
            else:
                units.append((cmd,))

    def run(cmd: tuple[str, ...]) -> str:
        if skip and cmd not in lockfile_cmds:
            return f"  - skip {' '.join(cmd)} (MAKEGHREPO_SKIP_LOCAL_CHECKS set; CI will run it)"
        if shutil.which(cmd[0]) is None:
            return f"  - skip {' '.join(cmd)} ({cmd[0]} not installed; CI will run it)"
        # After the sequential setup phase, `uv run` no longer needs to check
        # (or wait on another concurrent check) whether the venv is in sync.
        run_cmd = (cmd[0], cmd[1], "--no-sync", *cmd[2:]) if cmd[:2] == ("uv", "run") else cmd
        result = subprocess.run(run_cmd, cwd=dest, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"failed: {' '.join(cmd)}\n{result.stdout}{result.stderr}")
        return "  $ " + " ".join(cmd)

    for cmd in setup:
        log(run(cmd))

    if not units:
        return

    def run_unit(unit: tuple[tuple[str, ...], ...]) -> tuple[list[str], RuntimeError | None]:
        lines: list[str] = []
        for cmd in unit:
            try:
                lines.append(run(cmd))
            except RuntimeError as exc:
                return lines, exc
        return lines, None

    with ThreadPoolExecutor(max_workers=min(4, len(units))) as pool:
        results = list(pool.map(run_unit, units))  # preserves declared order, not completion order

    for lines, error in results:
        for line in lines:
            log(line)
        if error:
            raise error
