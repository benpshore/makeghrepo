"""Tiny git CLI helpers for tests (the project itself uses only the standard library)."""

import subprocess
from pathlib import Path


def git(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=check
    )


def out(path: Path, *args: str) -> str:
    return git(path, *args).stdout.strip()


def init(path: Path, *args: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", *args, str(path)], check=True)


def init_with_origin(path: Path, url: str) -> None:
    init(path)
    git(path, "remote", "add", "origin", url)
