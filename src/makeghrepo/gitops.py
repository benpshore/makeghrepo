"""Local git operations via GitPython."""

from __future__ import annotations

from pathlib import Path

import git


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
    """``git add --all && git commit -m <message>``."""
    repo = git.Repo(path)
    repo.git.add(all=True)
    # Use the git CLI (not index.commit) so hooks and commit signing are honored.
    repo.git.commit("-m", message)


def push_main(path: Path) -> None:
    git.Repo(path).git.push("-u", "origin", "main")


def remote_has_main(path: Path) -> bool:
    try:
        return bool(git.Repo(path).git.ls_remote("--heads", "origin", "main").strip())
    except git.GitCommandError:  # no origin yet, or unreachable
        return False
