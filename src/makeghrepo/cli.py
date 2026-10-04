"""Create or resume a project with one policy from parsing through publication.

The CLI is the junction between local project generation and GitHub enforcement.
With no options, both policy bools are False: templates describe required PRs/CI
and configuration builds both rules. With --no-ci, only the check requirement is
omitted; the generated workflows and the PR/review-thread requirement remain.
With --no-pr, direct pushes are permitted but still need CI unless --no-ci is
also selected. Keeping these choices independent avoids contradictory promises.

Offline rendering passes the choices straight to the shared template and stops.
Creation passes them to that same renderer, then records them before local checks
can fail. Resume restores the original marker choices before any mutation; it
never rewrites generated text or treats an omitted flag as a change of policy.
Finally, GitHub configuration applies the effective choices to new rules while
refusing to weaken stricter live policy. That last refusal matters: the marker is
our retry record, not authority to undo a repository owner's later decisions.
Rust follows this same flow, with shared templates and offline parity tests.
"""

from __future__ import annotations

import os
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated

import typer

from makeghrepo import names, registry

app = typer.Typer(add_completion=True, context_settings={"help_option_names": ["-h", "--help"]})
LANGS = registry.load()


class ProjectLicense(StrEnum):
    NONE = "none"
    MIT = "MIT"


def _version() -> str:
    try:
        return version("makeghrepo")
    except PackageNotFoundError:
        return "unknown"


def _show_version(value: bool) -> None:
    if value:
        typer.echo(f"makeghrepo {_version()}")
        raise typer.Exit()


def fail(msg: str) -> typer.Exit:
    typer.secho(msg, fg=typer.colors.RED, err=True)
    return typer.Exit(1)


def _project_name(raw: str, langs: list[str]) -> str:
    name = names.validate_name(names.normalize_name(raw))
    strict = [lang for lang in langs if LANGS[lang].leading_letter]
    if strict and not name[0].isalpha():
        raise ValueError(
            f"{name!r} can't be a {strict[0]} package name: it must start with a letter. "
            "Pick another name"
        )
    return name


def _resume_policy(marker: dict, no_ci: bool, no_pr: bool) -> tuple[bool, bool]:
    """Restore immutable creation choices; flags never migrate a live policy.

    A bool option's False default means "no opt-out requested on this invocation",
    not "undo the stored opt-out". Consequently a plain resume restores True from
    the marker, while an explicit True against a recorded False is a conflict.
    Missing fields come from older releases and must retain enforcement. Reject
    nulls, numbers and strings rather than letting truthiness loosen that default.
    Python and Rust intentionally implement this same two-field state machine.
    """
    recorded = []
    for key, requested in (("no_ci", no_ci), ("no_pr", no_pr)):
        value = marker.get(key, False)
        if not isinstance(value, bool):
            raise ValueError("invalid recorded creation policy; review the resume marker")
        if requested and not value:
            raise ValueError(
                f"a resume never changes creation policy; re-run without --{key.replace('_', '-')}"
            )
        recorded.append(value)
    return recorded[0], recorded[1]


@app.command(
    help="Create a new GitHub repo, fully configured. Re-run the same command to resume.\n\n"
    f"Languages (any number, or none): {', '.join(LANGS)}.",
)
def main(
    words: Annotated[
        list[str] | None, typer.Argument(metavar="[NAME] [LANGUAGE]...", show_default=False)
    ] = None,
    private: Annotated[bool, typer.Option("--private")] = False,
    no_ci: Annotated[
        bool,
        typer.Option(
            "--no-ci",
            help="Public only: do not require CI checks; workflows and local checks remain.",
        ),
    ] = False,
    no_pr: Annotated[
        bool,
        typer.Option(
            "--no-pr",
            help="Public only: do not require pull requests or PR reviews; "
            "CI remains required unless --no-ci.",
        ),
    ] = False,
    lib: Annotated[
        bool,
        typer.Option(
            "--lib", help="python: library layout, no console script (like uv init --lib)"
        ),
    ] = False,
    project_license: Annotated[
        ProjectLicense | None,
        typer.Option(
            "--license", help="Project license: none by default; MIT only when requested."
        ),
    ] = None,
    render: Annotated[
        Path | None,
        typer.Option(
            "--render", metavar="DIR", help="Render offline into DIR; no checks, repo or GitHub."
        ),
    ] = None,
    show_version: Annotated[
        bool,
        typer.Option("--version", callback=_show_version, is_eager=True, help="Show the version."),
    ] = False,
    owner: Annotated[str | None, typer.Option("--owner", hidden=True)] = None,
    author: Annotated[str | None, typer.Option("--author", hidden=True)] = None,
    description: Annotated[str | None, typer.Option("--description", hidden=True)] = None,
    year: Annotated[str | None, typer.Option("--year", hidden=True)] = None,
) -> None:
    # Copier imports its platform probe machinery; keep help/version independent
    # of that renderer and every external command as well as GitPython.
    from makeghrepo import scaffold

    words = list(words or [])
    # A leading non-language word is the name; otherwise pick a random one.
    raw_name = words.pop(0) if words and scaffold.language(words[0]) is None else None
    langs: list[str] = []
    for word in words:
        lang = scaffold.language(word)
        if lang is None:
            raise fail(f"unknown language {word!r}. Choose from: {', '.join(scaffold.LANGUAGES)}")
        langs += [lang] if lang not in langs else []
    if lib and "python" not in langs:
        raise fail("--lib only applies to python; add `python` to the language list")

    # Validate public-only semantics before rendering, authentication or Git init.
    # Private repositories already have a separate policy: disable Actions before
    # the initial push. Neither flag is permission to change that policy.
    if private and (no_ci or no_pr):
        raise fail("--no-ci and --no-pr only apply to public repositories")

    if render is not None:
        try:
            name = _project_name(raw_name if raw_name is not None else "quiet-otter", langs)
            offline_owner = names.validate_owner(owner if owner is not None else "someone")
            scaffold.render(render, {
                "project_name": name,
                "package_name": names.package_name(name),
                "description": description if description is not None else "a test project",
                "author_name": author if author is not None else "Test User",
                "github_owner": offline_owner,
                "year": year if year is not None else "2026",
                "private": private,
                "no_ci": no_ci,
                "no_pr": no_pr,
                "py_lib": lib,
                "project_license": (project_license or ProjectLicense.NONE).value,
                "languages": langs,
            })  # fmt: skip
        except (ValueError, OSError) as exc:
            raise fail(str(exc)) from exc
        typer.echo(f"rendered {render}")
        return
    if any(value is not None for value in (owner, author, description, year)):
        raise fail("--owner, --author, --description and --year require --render")

    # GitPython probes git when imported. Help, version and offline rendering
    # must also work on a machine without git or GitHub credentials.
    import git

    from makeghrepo import github, gitops

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

    want_private = private
    if resume:
        # makeghrepo git-inits right after rendering, so a dir without .git isn't ours.
        if not (dest / ".git").is_dir():
            raise fail(f"{dest} exists but isn't a git repo; pick another name")
        # Any git repo can sit at this path; only the marker proves makeghrepo made it.
        marker = gitops.read_marker(dest)
        if marker is None:
            raise fail(
                f"{dest} is a git repo makeghrepo didn't create "
                "(no .git/makeghrepo.json); pick another name"
            )
        if marker.get("owner") != owner or marker.get("name") != name:
            raise fail(
                f"{dest}'s recorded owner/name does not match {repo}; "
                "restore the original account and folder name before resuming"
            )
        try:
            gitops.validate_origin(dest, repo)
        except (RuntimeError, git.GitCommandError) as exc:
            raise fail(str(exc)) from exc
        if private and not marker.get("private"):
            raise fail(f"{repo} was created public; re-run without --private or pick another name")
        want_private = bool(marker.get("private"))
        # Read policy before smoke tests/commits/configuration: conflicting flags
        # must fail without changing either the project or its remote settings.
        try:
            no_ci, no_pr = _resume_policy(marker, no_ci, no_pr)
        except ValueError as exc:
            raise fail(str(exc)) from exc
        if want_private and (no_ci or no_pr):
            raise fail("--no-ci and --no-pr only apply to public repositories")
        # Older makeghrepo versions always generated MIT, before recording a choice.
        recorded_license = marker.get("license", "MIT")
        if recorded_license not in ("none", "MIT"):
            raise fail(f"{dest} has an invalid recorded license; review its resume marker")
        if project_license is not None and project_license.value != recorded_license:
            raise fail(
                f"{repo} was created with license {recorded_license}; "
                "a resume never changes licensing. Re-run without --license"
            )
        recorded = [str(lang) for lang in marker.get("languages") or []]
        if langs and langs != recorded:
            typer.echo(f"note: ignoring languages on resume; using {', '.join(recorded) or 'none'}")
        langs = recorded
        typer.echo(f"resuming {dest}")
    elif on_github:
        raise fail(f"{repo} already exists on GitHub")
    else:
        try:
            _project_name(name, langs)
        except ValueError as exc:
            raise fail(str(exc)) from exc
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
            "no_ci": no_ci,
            "no_pr": no_pr,
            "py_lib": lib,
            "project_license": (project_license or ProjectLicense.NONE).value,
            "languages": langs,
        })  # fmt: skip
        gitops.init(dest)
        # A fresh host or CI runner may have no git identity configured anywhere;
        # fall back to the authenticated GitHub user so `git commit` never fails on that.
        gitops.ensure_identity(dest, author_name, f"{owner}@users.noreply.github.com")
        # Written before the smoke test so an interrupted first run can still resume.
        # Store both bools even when False. Legacy absence means enforced, while
        # explicit True survives retries without requiring the user to repeat it.
        # This tool-owned marker is local Git metadata, never a committed secret.
        gitops.write_marker(dest, {
            "schema": 1,
            "name": name,
            "owner": owner,
            "private": private,
            "no_ci": no_ci,
            "no_pr": no_pr,
            "languages": langs,
            "lib": lib,
            "license": (project_license or ProjectLicense.NONE).value,
            "created_by": f"makeghrepo {_version()}",
        })  # fmt: skip

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
            actual_private = github.is_private(repo)
            if want_private and not actual_private:
                raise fail(
                    f"{repo} is public on GitHub, but this project was created private. "
                    "Refusing to publish; makeghrepo never changes visibility. "
                    "Restore the remote's private visibility before retrying"
                )
            private = actual_private
            if private and (no_ci or no_pr):
                raise fail("--no-ci and --no-pr only apply to public repositories")
        else:
            private = want_private
            github.create_repo(repo, dest, name, private)
    except github.GhError as exc:
        raise fail(f"{exc}\nLocal project is intact; re-run to retry.") from exc

    # Bootstrap only: a configuration rerun must never publish subsequent local work.
    try:
        pushed = on_github and gitops.remote_has_main(dest)
    except git.GitCommandError as exc:
        raise fail(
            f"{exc}\nCould not check remote main; refusing to push. "
            "Local project is intact; re-run to retry."
        ) from exc
    push = None if pushed else lambda: gitops.push_main(dest)
    failed = github.configure_all(
        repo, private=private, no_ci=no_ci, no_pr=no_pr, push=push, log=typer.echo
    )
    typer.echo(f"\nhttps://github.com/{repo}\ncd {dest}")
    if failed:
        typer.echo(f"{len(failed)} step(s) failed. Fix, then re-run: makeghrepo {name}")
        raise typer.Exit(2)
