"""makeghrepo command line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import git
import typer

from makeghrepo import github, gitops, names, scaffold

app = typer.Typer(
    help="Bootstrap a new uv project and publish it to GitHub, fully configured.",
    no_args_is_help=True,
)


def fail(msg: str) -> typer.Exit:
    typer.secho(msg, fg=typer.colors.RED, err=True)
    return typer.Exit(1)


@app.command()
def new(
    name: Annotated[
        str | None, typer.Argument(help="Repo name. Omit for a random one like 'quiet-otter'.")
    ] = None,
    description: Annotated[str, typer.Option("--description", "-d")] = "",
    base_dir: Annotated[
        Path, typer.Option("--dir", envvar="MAKEGHREPO_DIR", help="Parent directory.")
    ] = Path("~/code/GitHub"),
    owner: Annotated[
        str | None,
        typer.Option(envvar="MAKEGHREPO_OWNER", help="GitHub user/org. Default: gh's user."),
    ] = None,
    python: Annotated[str, typer.Option("--python", "-p")] = "3.14",
    private: Annotated[bool, typer.Option("--private")] = False,
    github_: Annotated[
        bool, typer.Option("--github/--local", help="--local skips everything on GitHub.")
    ] = True,
    project: Annotated[bool, typer.Option("--project/--no-project")] = True,
    checks: Annotated[
        bool, typer.Option("--checks/--skip-checks", help="Lock, lint and test before commit.")
    ] = True,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation.")] = False,
) -> None:
    """Create a project from a template, commit it, and publish it to GitHub."""
    base_dir = base_dir.expanduser().resolve()
    try:
        if github_ and not owner:
            owner = github.current_user()
        if owner:
            names.validate_owner(owner)
        names.validate_python_version(python)
        names.validate_description(description)

        def is_taken(n: str) -> bool:
            return (base_dir / n).exists() or (github_ and github.repo_exists(f"{owner}/{n}"))

        if name:
            name = names.validate_name(names.normalize_name(name))
            if is_taken(name):
                raise ValueError(f"{name} already exists in {base_dir} or on GitHub")
        else:
            name = names.unique_random_name(is_taken)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise fail(str(exc)) from exc

    dest = base_dir / name
    repo = f"{owner}/{name}"
    typer.echo(f"project: {dest}")
    visibility = "private" if private else "public"
    typer.echo(f"github:  {repo} ({visibility})" if github_ else "github:  skipped (--local)")
    if not yes and not typer.confirm("Proceed?", default=True):
        raise typer.Exit(1)

    # Only the name: an email would be published in pyproject.toml.
    author = gitops.author_from_git_config()[0] or owner or ""
    scaffold.render("python", dest, {
        "project_name": name,
        "package_name": names.package_name(name),
        "description": description or name,
        "author_name": author,
        "python_version": python,
        "private": private,
    })  # fmt: skip

    try:
        if checks:
            typer.echo("smoke testing generated project…")
            scaffold.smoke_test(dest, typer.echo)
        typer.echo("git init -b main && git add --all && git commit -m setup")
        gitops.init_and_commit(dest, "setup")
    except (RuntimeError, git.GitCommandError) as exc:
        raise fail(f"{exc}\nNothing was published. Inspect or delete {dest}.") from exc

    if not github_:
        typer.echo(f"done: {dest}")
        return

    try:
        github.create_repo(repo, dest, description or name, private)
    except github.GhError as exc:
        raise fail(f"{exc}\nLocal project is intact at {dest}.") from exc
    failed = github.configure_all(
        repo, private=private, project=project, push=lambda: gitops.push_main(dest), log=typer.echo
    )
    _finish(repo, failed, dest)


@app.command()
def configure(
    repo: Annotated[str, typer.Argument(help="OWNER/REPO of an existing repo.")],
    project: Annotated[bool, typer.Option("--project/--no-project")] = True,
) -> None:
    """(Re)apply GitHub settings to an existing repo. Safe to re-run."""
    owner, _, name = repo.partition("/")
    try:
        names.validate_owner(owner)
        names.validate_name(name)
        private = github.is_private(repo)
    except (ValueError, github.GhError) as exc:
        raise fail(f"expected an existing OWNER/REPO: {exc}") from exc
    _finish(repo, github.configure_all(repo, private=private, project=project, log=typer.echo))


def _finish(repo: str, failed: list[str], dest: Path | None = None) -> None:
    typer.echo(f"\nhttps://github.com/{repo}")
    if dest:
        typer.echo(f'cd {dest}  # or: tmux new-session -A -s main -n {dest.name} -c "{dest}"')
    if failed:
        if "push main" in failed and dest:
            typer.echo(f"push failed. Fix, then:\n  git -C {dest} push -u origin main")
        typer.echo(f"{len(failed)} step(s) failed. Re-run:\n  makeghrepo configure {repo}")
        raise typer.Exit(2)


def new_main() -> None:
    """Entry point for the `ghnew` shortcut: same as `makeghrepo new`."""
    typer.run(new)
