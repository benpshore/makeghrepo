"""Local git operations via GitPython."""

from __future__ import annotations

import json
import re
from pathlib import Path

import git

MARKER = "makeghrepo.json"  # lives inside .git, so it is never committed or pushed


# Same pattern as the `repo` job in the generated ci.yml (a test keeps them in sync).
JUNK_PATTERN = r"(^|/)(\.DS_Store|\.env(\..+)?|[^/]+\.(db|sqlite3?|duckdb|pem|key|p12|pfx))$"
_JUNK = re.compile(JUNK_PATTERN, re.IGNORECASE)


def junk_files(names: list[str]) -> list[str]:
    """The paths CI would reject: OS junk, databases, private keys and .env files."""
    return [name for name in names if _JUNK.search(name) and not name.endswith(".env.example")]


def write_marker(path: Path, data: dict[str, object]) -> None:
    """Record that makeghrepo created this repo, and with what intent (visibility, languages)."""
    (path / ".git" / MARKER).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def read_marker(path: Path) -> dict[str, object] | None:
    """The marker written by write_marker, or None if missing, unreadable or not schema 1."""
    try:
        data = json.loads((path / ".git" / MARKER).read_text())
    except OSError, ValueError:
        return None
    if not isinstance(data, dict) or data.get("schema") != 1:
        return None
    langs = data.get("languages")
    if not isinstance(data.get("private"), bool) or not isinstance(langs, list):
        return None
    if not all(isinstance(lang, str) for lang in langs):
        return None
    return data


def author_from_git_config() -> tuple[str, str]:
    """Return (name, email) from the user's global git config, or empty strings."""
    reader = git.GitConfigParser(git.config.get_config_path("global"), read_only=True)
    return (
        str(reader.get_value("user", "name", default="")),
        str(reader.get_value("user", "email", default="")),
    )


def init(path: Path) -> None:
    git.Repo.init(path, initial_branch="main")


def ensure_identity(path: Path, name: str, email: str) -> None:
    """Set commit identity on this repo only, if none is set at any config level.

    A fresh host or CI runner often has no ``user.name``/``user.email`` configured
    anywhere, and ``git commit`` refuses to run without one. Shell out to ``git
    config`` itself (not GitPython's own config parser, which resolves file paths
    independently of ``git``'s ``GIT_CONFIG_*`` env vars and can disagree with what
    ``git commit`` actually sees) so the check matches reality. Never touch the
    user's global config: a plain ``git config <key> <value>`` writes to this
    repo's own ``.git/config``.
    """
    repo = git.Repo(path)
    for key, value in (("user.name", name), ("user.email", email)):
        try:
            repo.git.config("--get", key)
        except git.GitCommandError:
            repo.git.config(key, value)


def has_commits(path: Path) -> bool:
    return git.Repo(path).head.is_valid()


def commit_all(path: Path, message: str = "setup") -> None:
    """``git add --all && git commit -m <message>``, refusing anything CI would reject."""
    repo = git.Repo(path)
    repo.git.add(all=True)
    junk = junk_files(repo.git.diff("--cached", "--name-only").splitlines())
    if junk:
        raise RuntimeError(f"refusing to commit junk or secrets: {', '.join(junk)}")
    # Use the git CLI (not index.commit) so hooks and commit signing are honored.
    repo.git.commit("-m", message)


def push_main(path: Path) -> None:
    git.Repo(path).git.push("-u", "origin", "main")


def validate_origin(path: Path, full_name: str) -> None:
    """Refuse a resume whose effective fetch or push URLs target another repo.

    A missing origin is normal after a failed create; create_repo adds it later.
    Ask git for expanded URLs so pushurl and insteadOf rewrites are checked too.
    Never print an unexpected URL: it could contain embedded credentials.
    """
    repo = git.Repo(path)
    if "origin" not in repo.remotes:
        return
    allowed = {
        f"{prefix}{full_name}{suffix}".casefold()
        for prefix in (
            "https://github.com/",
            "git@github.com:",
            "ssh://git@github.com/",
            "ssh://git@github.com:22/",
            "ssh://git@ssh.github.com:443/",
        )
        for suffix in ("", ".git")
    }
    for options in (("--all",), ("--push", "--all")):
        urls = repo.git.remote("get-url", *options, "origin").splitlines()
        if not urls or any(url.rstrip("/").casefold() not in allowed for url in urls):
            raise RuntimeError(
                f"origin does not match {full_name}; restore its GitHub fetch and push URLs "
                "before resuming"
            )


def remote_has_main(path: Path) -> bool:
    try:
        return bool(git.Repo(path).git.ls_remote("--heads", "origin", "main").strip())
    except git.GitCommandError:  # no origin yet, or unreachable
        return False
