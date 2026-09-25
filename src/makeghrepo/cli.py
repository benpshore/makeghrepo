"""makeghrepo command line interface."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Annotated

import typer

from makeghrepo import github, gitops, names, scaffold

DEFAULT_BASE_DIR = Path.home() / "code" / "GitHub"

app = typer.Typer(
    help="Bootstrap a new uv project and publish it to GitHub, fully configured.",
    no_args_is_help=True,
    add_completion=True,
)


def say(msg: str) -> None:
    typer.echo(msg)


def fail(msg: str, code: int = 1) -> typer.Exit:
    typer.secho(msg, fg=typer.colors.RED, err=True)
    return typer.Exit(code)


@app.command()
def new(
    name: Annotated[
        str | None, typer.Argument(help="Repo name. Omit for a random one like 'quiet-otter'.")
    ] = None,
    description: Annotated[str, typer.Option("--description", "-d")] = "",
    base_dir: Annotated[
        Path,
        typer.Option("--dir", envvar="MAKEGHREPO_DIR", help="Parent directory for the project."),
    ] = DEFAULT_BASE_DIR,
    owner: Annotated[
        str | None,
        typer.Option(envvar="MAKEGHREPO_OWNER", help="GitHub user/org. Default: gh's user."),
    ] = None,
    template: Annotated[str, typer.Option("--template", "-t")] = "python",
    python: Annotated[str, typer.Option("--python", "-p")] = "3.14",
    private: Annotated[bool, typer.Option("--private", help="Create a private repo.")] = False,
    github_: Annotated[
        bool, typer.Option("--github/--local", help="--local skips everything on GitHub.")
    ] = True,
    project: Annotated[bool, typer.Option("--project/--no-project")] = True,
    checks: Annotated[
        bool, typer.Option("--checks/--skip-checks", help="Lock, lint and test before commit.")
    ] = True,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Render locally; only print GitHub calls.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation.")] = False,
) -> None:
    """Create a project from a template, commit it, and publish it to GitHub."""
    gh = github.Gh(dry_run=dry_run, log=say)
    base_dir = base_dir.expanduser().resolve()

    if github_ and not owner:
        try:
            owner = gh.current_user()
        except (github.GhError, FileNotFoundError) as exc:
            raise fail(f"can't determine GitHub user (try `makeghrepo doctor`):\n{exc}") from exc
    owner = owner or "you"

    def is_taken(candidate: str) -> bool:
        if (base_dir / candidate).exists():
            return True
        return github_ and not dry_run and gh.repo_exists(f"{owner}/{candidate}")

    if name:
        try:
            name = names.validate_name(names.normalize_name(name))
        except ValueError as exc:
            raise fail(str(exc)) from exc
        if is_taken(name):
            raise fail(f"{name} already exists locally in {base_dir} or on GitHub under {owner}")
    else:
        name = names.unique_random_name(is_taken)

    dest = base_dir / name
    full_name = f"{owner}/{name}"
    description = description or name
    author_name, author_email = gitops.author_from_git_config()

    say(f"project:  {dest}")
    say(f"template: {template} (python {python})")
    if github_:
        visibility = "private" if private else "public"
        say(f"github:   {full_name} ({visibility}){'  [dry-run]' if dry_run else ''}")
    else:
        say("github:   skipped (--local)")
    if not yes and not typer.confirm("Proceed?", default=True):
        raise typer.Exit(1)

    say("rendering template…")
    try:
        scaffold.render(
            template,
            dest,
            {
                "project_name": name,
                "package_name": names.package_name(name),
                "description": description,
                "author_name": author_name or owner,
                "author_email": author_email,
                "github_owner": owner,
                "python_version": python,
            },
        )
    except (FileExistsError, ValueError) as exc:
        raise fail(str(exc)) from exc

    if checks:
        say("smoke testing generated project…")
        try:
            scaffold.smoke_test(dest, say)
        except RuntimeError as exc:
            raise fail(
                f"{exc}\nProject left at {dest} for inspection; nothing was published."
            ) from exc

    say("git init -b main && git add --all && git commit -m setup")
    gitops.init_and_commit(dest, "setup")

    if not github_:
        say(f"done: {dest}")
        return

    say(f"creating {full_name} on GitHub…")
    try:
        github.create_repo(gh, full_name, dest, description, private)
    except github.GhError as exc:
        raise fail(f"gh repo create failed; local project is intact at {dest}\n{exc}") from exc

    say("configuring repository…")
    report = github.configure_all(gh, full_name, project=project)
    _summarize(report, full_name)
    say(f"\ncd {dest}")
    say(f'tmux new-session -A -s main -n {name} -c "{dest}"')
    if report.failed:
        raise typer.Exit(2)


@app.command()
def configure(
    full_name: Annotated[str, typer.Argument(help="OWNER/REPO of an existing repo.")],
    project: Annotated[bool, typer.Option("--project/--no-project")] = True,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """(Re)apply GitHub settings to an existing repo. Safe to re-run."""
    if full_name.count("/") != 1:
        raise fail("expected OWNER/REPO")
    gh = github.Gh(dry_run=dry_run, log=say)
    report = github.configure_all(gh, full_name, project=project)
    _summarize(report, full_name)
    if report.failed:
        raise typer.Exit(2)


@app.command()
def doctor() -> None:
    """Check that git, uv and gh are installed and gh has the scopes we need."""
    problems = 0

    def check(ok: bool, label: str, hint: str = "") -> None:
        nonlocal problems
        problems += not ok
        say(f"{'✓' if ok else '✗'} {label}" + ("" if ok or not hint else f"\n    → {hint}"))

    for tool in ("git", "uv", "gh"):
        check(shutil.which(tool) is not None, f"{tool} on PATH", f"brew install {tool}")
    name, email = gitops.author_from_git_config()
    check(
        bool(name and email),
        "git user.name / user.email set",
        'git config --global user.name "…"; git config --global user.email "…"',
    )

    gh = github.Gh(log=say)
    try:
        user = gh.current_user()
        check(True, f"gh authenticated as {user}")
        scopes = gh.token_scopes()
    except (github.GhError, FileNotFoundError) as exc:
        check(False, "gh authenticated", f"gh auth login  ({exc})")
        raise typer.Exit(1) from exc

    if scopes:
        missing = github.REQUIRED_SCOPES - scopes
        check(
            not missing,
            f"token scopes include {sorted(github.REQUIRED_SCOPES)}",
            f"gh auth refresh -s {','.join(sorted(missing))}",
        )
        for scope, why in github.OPTIONAL_SCOPES.items():
            if scope not in scopes:
                say(f"! optional scope '{scope}' missing ({why}) → gh auth refresh -s {scope}")
    else:
        say("! couldn't read token scopes (fine-grained token?); skipping scope check")
    if os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        say("! GH_TOKEN/GITHUB_TOKEN is set and overrides your gh login")
    raise typer.Exit(1 if problems else 0)


def _summarize(report: github.StepReport, full_name: str) -> None:
    say(f"\nhttps://github.com/{full_name}")
    if report.failed:
        say(f"{len(report.failed)} step(s) failed. Fix, then re-run:")
        say(f"  makeghrepo configure {full_name}")


def new_main() -> None:
    """Entry point for the `ghnew` shortcut: same as `makeghrepo new`."""
    typer.run(new)
