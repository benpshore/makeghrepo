"""Render the copier template and smoke-test the generated project."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from importlib.resources import as_file, files
from pathlib import Path

import copier

from makeghrepo import registry

# Everything per-language lives in src/makeghrepo/langs/<id>.toml (see registry.py);
# these tables are derived from it so the rest of the code keeps its shape.
LANGS = registry.load()
LANGUAGES = tuple(LANGS)
ALIASES = {alias: lang.id for lang in LANGS.values() for alias in lang.aliases}

# Ship as separate, sometimes-missing plugin binaries (cargo-fmt, cargo-clippy)
# rather than being built into cargo itself — `cargo <name>` fails outright
# with "no such command" if `cargo-<name>` isn't on PATH, even though `cargo`
# itself is (e.g. an apt-installed rustc/cargo with no rustup components).
CARGO_PLUGIN_SUBCOMMANDS = {"fmt", "clippy"}

# Local checks per language. Each is skipped if its tool isn't installed (CI still runs it).
# postgres has none: its CI job applies the migrations to a real database.
CHECKS: dict[str, tuple[tuple[str, ...], ...]] = {
    lang.id: lang.checks for lang in LANGS.values() if lang.checks
}

# These create lockfiles that CI (`--locked`, `npm ci`) and the Dockerfiles depend on.
# Each is its language's first CHECKS command, so it can't drift out of sync.
REQUIRED_TOOLS = {lang.id: lang.checks[0][0] for lang in LANGS.values() if lang.required_tool}

# How many of each language's leading CHECKS commands must run sequentially,
# in that order, before the rest of that language's checks are safe to start:
# `uv sync`/`npm install` have to finish before anything reads the environment
# they build. Everything after that prefix is independent of the others (and
# of every other requested language) and can run concurrently.
#
# Any language not listed here contributes its *entire* CHECKS tuple as one
# sequential unit instead (see smoke_test) — safe-by-default, since a language
# added later without being taught how to split safely (e.g. cargo/cmake,
# which share a build cache a concurrent invocation of the same tool would
# fight over) would otherwise silently become "fully poolable" by omission.
SETUP_LEN = {lang.id: lang.setup_len for lang in LANGS.values() if lang.setup_len}


def language(word: str) -> str | None:
    word = word.lower()
    return word if word in LANGUAGES else ALIASES.get(word)


def render(dest: Path, data: dict[str, object]) -> None:
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"{dest} already exists and is not empty")
    chosen = list(data.get("languages") or [])
    with as_file(files("makeghrepo") / "templates" / "project") as src:
        copier.run_copy(
            str(src),
            str(dest),
            data={
                "year": str(date.today().year),
                **registry.derived(LANGS, chosen),
                **data,
            },
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
    else assumes they finished. What's left runs concurrently, one worker per
    unit (a unit is either a single independent command, or — for a language
    not in ``SETUP_LEN``, see there — that whole language's commands, kept in
    order). Once any command anywhere fails, every other in-flight unit stops
    before its *next* command rather than continuing to burn CPU on a fanless
    host for a result that's already going to be discarded; a command already
    running when that happens still finishes (Python can't safely interrupt a
    running subprocess.run from another thread). Reporting stays deterministic
    despite that: results still stream out, and the first *real* failure gets
    raised, in the order units were declared, not the order they finished.
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
        if lang not in SETUP_LEN:
            whole = tuple(c for c in cmds if c not in seen)
            seen.update(whole)
            if whole:
                units.append(whole)
            continue
        for i, cmd in enumerate(cmds):
            if cmd in seen:
                continue
            seen.add(cmd)
            if i < SETUP_LEN[lang]:
                setup.append(cmd)
            else:
                units.append((cmd,))

    def run_cmd_for(cmd: tuple[str, ...]) -> tuple[str, ...]:
        # After the sequential setup phase, `uv run` no longer needs to check
        # (or wait on another concurrent check) whether the venv is in sync.
        return (cmd[0], cmd[1], "--no-sync", *cmd[2:]) if cmd[:2] == ("uv", "run") else cmd

    def missing_tool(cmd: tuple[str, ...]) -> str | None:
        """The binary name cmd actually needs, if it's not on PATH, else None."""
        if shutil.which(cmd[0]) is None:
            return cmd[0]
        if cmd[0] == "cargo" and len(cmd) > 1 and cmd[1] in CARGO_PLUGIN_SUBCOMMANDS:
            plugin = f"cargo-{cmd[1]}"
            return plugin if shutil.which(plugin) is None else None
        return None

    def announce(cmd: tuple[str, ...]) -> str | None:
        """A skip message if cmd won't actually run, else None."""
        if skip and cmd not in lockfile_cmds:
            return f"  - skip {' '.join(cmd)} (MAKEGHREPO_SKIP_LOCAL_CHECKS set; CI will run it)"
        missing = missing_tool(cmd)
        if missing is not None:
            return f"  - skip {' '.join(cmd)} ({missing} not installed; CI will run it)"
        return None

    def execute(cmd: tuple[str, ...]) -> None:
        """Run cmd for real (already past the skip checks). Raises RuntimeError."""
        run_cmd = run_cmd_for(cmd)
        result = subprocess.run(run_cmd, cwd=dest, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"failed: {' '.join(run_cmd)}\n{result.stdout}{result.stderr}")

    for cmd in setup:
        msg = announce(cmd)
        if msg is not None:
            log(msg)
            continue
        log("  $ " + " ".join(run_cmd_for(cmd)))
        execute(cmd)

    if not units:
        return

    stop_event = threading.Event()

    def run_unit(unit: tuple[tuple[str, ...], ...]) -> tuple[list[str], RuntimeError | None]:
        lines: list[str] = []
        for cmd in unit:
            if stop_event.is_set():
                lines.append(f"  - not run: {' '.join(cmd)} (another check failed)")
                break
            msg = announce(cmd)
            if msg is not None:
                lines.append(msg)
                continue
            run_cmd = run_cmd_for(cmd)
            lines.append("  $ " + " ".join(run_cmd))
            try:
                execute(cmd)
            except Exception as exc:  # any failure stops this unit and signals the rest
                stop_event.set()
                return lines, exc if isinstance(exc, RuntimeError) else RuntimeError(str(exc))
        return lines, None

    with ThreadPoolExecutor(max_workers=min(4, len(units))) as pool:
        for lines, error in pool.map(run_unit, units):  # lazy: streams in declared order
            for line in lines:
                log(line)
            if error:
                raise error
