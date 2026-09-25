"""Render the copier template, lock deps, and smoke-test the generated project."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from datetime import date
from importlib.resources import as_file, files
from pathlib import Path

import copier

TEMPLATES = ("python",)

SMOKE_CHECKS: tuple[tuple[str, ...], ...] = (
    ("uv", "lock"),
    ("uv", "sync", "--locked"),
    ("uv", "run", "ruff", "format", "--check"),
    ("uv", "run", "ruff", "check"),
    ("uv", "run", "pytest", "-q"),
    ("uv", "audit", "--locked", "--preview-features", "audit-command"),
    ("uv", "build", "-q"),
)


def render(template: str, dest: Path, data: dict[str, str]) -> None:
    if template not in TEMPLATES:
        raise ValueError(f"unknown template {template!r}; choose from {TEMPLATES}")
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"{dest} already exists and is not empty")
    with as_file(files("makeghrepo") / "templates" / template) as src:
        copier.run_copy(
            str(src),
            str(dest),
            data={"year": str(date.today().year), **data},
            defaults=True,
            unsafe=False,
            quiet=True,
            overwrite=False,
        )


def smoke_test(dest: Path, log: Callable[[str], None]) -> None:
    """Lock, install, lint, test, audit and build the freshly generated project."""
    for cmd in SMOKE_CHECKS:
        log("  $ " + " ".join(cmd))
        result = subprocess.run(cmd, cwd=dest, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"smoke check failed: {' '.join(cmd)}\n{result.stdout}{result.stderr}"
            )
