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


def init_and_commit(path: Path, message: str = "setup") -> None:
    """``git init -b main && git add --all && git commit -m <message>``."""
    repo = git.Repo.init(path, initial_branch="main")
    repo.git.add(all=True)
    # Use the git CLI (not index.commit) so hooks and commit signing are honored.
    repo.git.commit("-m", message)


def push_main(path: Path) -> None:
    git.Repo(path).git.push("-u", "origin", "main")
