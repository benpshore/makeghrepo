"""makeghrepo [NAME] [LANGUAGE]... [--private]"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import git
import typer

from makeghrepo import github, gitops, names, scaffold

app = typer.Typer(add_completion=True)
CODEQL = Path(".github/workflows/codeql.yml")  # rendered for public repos only


def fail(msg: str) -> typer.Exit:
    typer.secho(msg, fg=typer.colors.RED, err=True)
    return typer.Exit(1)


@app.command(
    help="Create a new GitHub repo, fully configured. Re-run the same command to resume.\n\n"
    f"Languages (any number, or none): {', '.join(scaffold.LANGUAGES)}.",
)
def main(
    words: Annotated[
        list[str] | None, typer.Argument(metavar="[NAME] [LANGUAGE]...", show_default=False)
    ] = None,
    private: Annotated[bool, typer.Option("--private")] = False,
) -> None:
    words = list(words or [])
    # A leading non-language word is the name; otherwise pick a random one.
    raw_name = words.pop(0) if words and scaffold.language(words[0]) is None else None
    langs: list[str] = []
    for word in words:
        lang = scaffold.language(word)
        if lang is None:
            raise fail(f"unknown language {word!r}. Choose from: {', '.join(scaffold.LANGUAGES)}")
        langs += [lang] if lang not in langs else []

    base_dir = Path(os.environ.get("MAKEGHREPO_DIR", "~/code/GitHub")).expanduser().resolve()
    try:
        owner = names.validate_owner(github.current_user())
        if raw_name:
            name = names.validate_name(names.normalize_name(raw_name))
        else:
            name = names.unique_random_name(
                lambda n: (base_dir / n).exists() or github.repo_exists(f"{owner}/{n}")
            )
        repo = f"{owner}/{name}"
        dest = base_dir / name
        resume = dest.exists()
        on_github = github.repo_exists(repo)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise fail(str(exc)) from exc

    if resume:
        # makeghrepo git-inits right after rendering, so a dir without .git isn't ours.
        if not (dest / ".git").is_dir():
            raise fail(f"{dest} exists but isn't a git repo; pick another name")
        typer.echo(f"resuming {dest}")
    elif on_github:
        raise fail(f"{repo} already exists on GitHub")
    else:
        typer.echo(f"creating {dest} [{', '.join(langs) or 'any language'}]")
        author_name = gitops.author_from_git_config()[0] or owner
        scaffold.render(dest, {
            "project_name": name,
            "package_name": names.package_name(name),
            "description": name,
            # Only the name: an email would be published in the repo.
            "author_name": author_name,
            "github_owner": owner,
            "private": private,
            "languages": langs,
        })  # fmt: skip
        gitops.init(dest)
        # A fresh host or CI runner may have no git identity configured anywhere;
        # fall back to the authenticated GitHub user so `git commit` never fails on that.
        gitops.ensure_identity(dest, author_name, f"{owner}@users.noreply.github.com")

    if not resume or not gitops.has_commits(dest):  # earlier check/commit failed
        try:
            scaffold.smoke_test(dest, langs, typer.echo)
            gitops.commit_all(dest, "setup")
        except (RuntimeError, git.GitCommandError) as exc:
            raise fail(
                f"{exc}\nNothing was published. Fix it, then re-run the same command."
            ) from exc

    try:
        if on_github:
            private = github.is_private(repo)
        else:
            if resume:
                # The rendered files remember whether it was meant to be private
                # (copier only writes .copier-answers.yml if a template opts in).
                private = private or not (dest / CODEQL).exists()
            github.create_repo(repo, dest, name, private)
    except github.GhError as exc:
        raise fail(f"{exc}\nLocal project is intact; re-run to retry.") from exc

    # Push only if main isn't on GitHub yet; once protected, main only changes via PRs.
    pushed = on_github and gitops.remote_has_main(dest)
    push = None if pushed else lambda: gitops.push_main(dest)
    failed = github.configure_all(repo, private=private, push=push, log=typer.echo)
    typer.echo(f"\nhttps://github.com/{repo}\ncd {dest}")
    if failed:
        typer.echo(f"{len(failed)} step(s) failed. Fix, then re-run: makeghrepo {name}")
        raise typer.Exit(2)
