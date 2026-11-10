"""makeghrepo [NAME] [LANGUAGE]... [--private]"""

from __future__ import annotations

import os
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated

import typer

from makeghrepo import inputs, names

app = typer.Typer(add_completion=True, context_settings={"help_option_names": ["-h", "--help"]})
LANGS = inputs.LANGS


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


def _dry_run(
    raw_name: str | None,
    langs: list[str],
    *,
    private: bool,
    lib: bool,
    project_license: ProjectLicense | None,
    render: Path | None,
    owner: str | None,
    render_only: tuple[str | None, ...],
) -> None:
    """Print the bootstrap plan without writing files, running commands or using the network.

    Offline by design (#154): the GitHub account, name availability and resume
    state are reported as unverified instead of looked up.
    """
    if render is not None:
        raise fail("--dry-run cannot be combined with --render; --render writes preview files")
    if any(value is not None for value in render_only):
        raise fail("--author, --description and --year require --render")
    if owner is not None:
        try:
            names.validate_owner(owner)
        except ValueError as exc:
            raise fail(str(exc)) from exc
    base_dir = Path(os.environ.get("MAKEGHREPO_DIR", "~/code/GitHub")).expanduser().resolve()
    if raw_name is None:
        name, dest, exists = "random, chosen at creation", base_dir / "<random name>", False
    else:
        try:
            name = _project_name(raw_name, langs)
        except ValueError as exc:
            raise fail(str(exc)) from exc
        dest = base_dir / name
        exists = dest.exists()
    typer.echo("Dry run only: no files or directories were written; no network requests were made.")
    typer.echo(f"Project: {name}")
    typer.echo(f"Languages: {', '.join(langs) or 'any language'}")
    typer.echo(f"Destination: {dest}")
    resume_note = "yes (a real run would try to resume it)" if exists else "no"
    typer.echo(f"Destination already exists: {resume_note}")
    typer.echo(f"Visibility requested: {'private' if private else 'public'}")
    typer.echo(f"Owner requested: {owner or 'the authenticated user (looked up at creation)'}")
    typer.echo(f"License requested: {(project_license or ProjectLicense.NONE).value}")
    typer.echo(f"Python library layout: {'yes' if lib else 'no'}")
    typer.echo(
        "Plan: look up the GitHub account and remote state, then render the project or "
        "load its recorded resume. For a fresh or uncommitted project, run dependency "
        "setup, commit a clean main snapshot, then run local validation. An unpublished "
        "committed resume validates its existing clean snapshot again. Preflight GitHub "
        "setup, create the remote if needed, apply and read back every required setting, "
        "then publish the selected snapshot. A settings-only resume does not commit or "
        "republish later work."
    )
    typer.echo(
        "If validation fails, the local commit is retained. Review and commit intended "
        "fixes before retrying."
    )
    typer.echo(
        "Unverified: GitHub identity/authentication, the owner's existence and type, "
        "repository availability and remote state, local resume metadata, and local tool "
        "availability."
    )


@app.command(
    help="Create a new GitHub repo, fully configured. Re-run the same command to resume.\n\n"
    f"Languages (any number, or none): {', '.join(LANGS)}.",
)
def main(
    words: Annotated[
        list[str] | None, typer.Argument(metavar="[NAME] [LANGUAGE]...", show_default=False)
    ] = None,
    private: Annotated[bool, typer.Option("--private")] = False,
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
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            "-n",
            help="Show the plan; write nothing, run nothing, no network. Not --render.",
        ),
    ] = False,
    show_version: Annotated[
        bool,
        typer.Option("--version", callback=_show_version, is_eager=True, help="Show the version."),
    ] = False,
    owner: Annotated[
        str | None,
        typer.Option(
            "--owner",
            metavar="LOGIN",
            help="Account or organization that owns the new repo (default: you, or "
            "$MAKEGHREPO_OWNER). With --render: the owner name in the preview.",
        ),
    ] = None,
    author: Annotated[str | None, typer.Option("--author", hidden=True)] = None,
    description: Annotated[str | None, typer.Option("--description", hidden=True)] = None,
    year: Annotated[str | None, typer.Option("--year", hidden=True)] = None,
) -> None:
    try:
        raw_name, langs = inputs.parse_words(list(words or []))
        # Reject invalid names before any authenticated gh operation. Normalization
        # is retained for compatibility; it never selects a different action. Apply
        # language-specific restrictions later: a resume uses its recorded languages.
        if raw_name is not None:
            names.validate_name(names.normalize_name(raw_name))
    except ValueError as exc:
        raise fail(str(exc)) from exc
    if lib and "python" not in langs:
        raise fail("--lib only applies to python; add `python` to the language list")
    if dry_run:
        _dry_run(
            raw_name,
            langs,
            private=private,
            lib=lib,
            project_license=project_license,
            render=render,
            owner=owner,
            render_only=(author, description, year),
        )
        return

    # Only validated positional actions reach the renderer. Copier remains the
    # rendering implementation; parsing/help/version need none of its probes.
    from makeghrepo import scaffold

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
                "github_org": False,  # offline: no lookup, so previews show the personal text
                "year": year if year is not None else "2026",
                "private": private,
                "py_lib": lib,
                "project_license": (project_license or ProjectLicense.NONE).value,
                "languages": langs,
            })  # fmt: skip
        except (ValueError, OSError) as exc:
            raise fail(str(exc)) from exc
        typer.echo(f"rendered {render}")
        return
    if any(value is not None for value in (author, description, year)):
        raise fail("--author, --description and --year require --render")

    # Help, version and offline rendering must work on a machine without git or
    # GitHub credentials, so these are imported only once a real run starts.
    from makeghrepo import github, gitops

    base_dir = Path(os.environ.get("MAKEGHREPO_DIR", "~/code/GitHub")).expanduser().resolve()
    try:
        # The commit identity is always the authenticated user; the repository
        # owner may instead be an organization that user can create repos in.
        user = names.validate_owner(github.current_user())
        owner = names.validate_owner(owner or os.environ.get("MAKEGHREPO_OWNER") or user)
        org = False
        if owner.lower() != user.lower():
            if github.owner_type(owner) != "Organization":
                raise ValueError(
                    f"--owner {owner} is another user's account; only your own account "
                    "or an organization can own the new repository"
                )
            org = True
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
        except RuntimeError as exc:  # includes gitops.GitError
            raise fail(str(exc)) from exc
        if private and not marker.get("private"):
            raise fail(f"{repo} was created public; re-run without --private or pick another name")
        want_private = bool(marker.get("private"))
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
        if any(lang not in LANGS for lang in recorded):
            raise fail(f"{dest} has an unknown recorded language; review its resume marker")
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
        author_name = gitops.author_from_git_config()[0] or user
        scaffold.render(dest, {
            "project_name": name,
            "package_name": names.package_name(name),
            "description": name,
            # Only the name: an email would be published in the repo.
            "author_name": author_name,
            "github_owner": owner,
            "github_org": org,
            "private": private,
            "py_lib": lib,
            "project_license": (project_license or ProjectLicense.NONE).value,
            "languages": langs,
        })  # fmt: skip
        gitops.init(dest)
        # A fresh host or CI runner may have no git identity configured anywhere;
        # fall back to the authenticated GitHub user so `git commit` never fails on that.
        gitops.ensure_identity(dest, author_name, f"{user}@users.noreply.github.com")
        # Written before the smoke test so an interrupted first run can still resume.
        gitops.write_marker(dest, {
            "schema": 1,
            "name": name,
            "owner": owner,
            "private": private,
            "languages": langs,
            "lib": lib,
            "license": (project_license or ProjectLicense.NONE).value,
            "created_by": f"makeghrepo {_version()}",
        })  # fmt: skip

    publication_head = None
    if not resume or not gitops.has_commits(dest):  # earlier check/commit failed

        def select_snapshot() -> None:
            nonlocal publication_head
            publication_head = gitops.commit_all(dest, "setup")
            gitops.require_bootstrap_head(dest, publication_head)

        try:
            # Setup must finish first so generated lockfiles enter the snapshot.
            # Never stage again after checks: a late edit must not become tested
            # merely because smoke_test returned successfully.
            scaffold.smoke_test(dest, langs, typer.echo, before_validation=select_snapshot)
            if publication_head is None:
                raise RuntimeError("no selected bootstrap snapshot")
            gitops.require_bootstrap_head(dest, publication_head)
        except RuntimeError as exc:  # includes gitops.GitError
            raise fail(
                f"{exc}\nThis invocation stopped before its GitHub creation/push steps.\n"
                f"Earlier lookup {'found a repository' if on_github else 'found no repository'} "
                f"at https://github.com/{repo}; this is not a publication history check.\n"
                f"Local project retained at {dest}. Fix it, commit any edits on main, "
                "then re-run the same command."
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
        else:
            private = want_private
    except github.GhError as exc:
        raise fail(
            f"{exc}\nRemote visibility lookup failed; "
            "creation/push was not attempted by this invocation.\n"
            f"Inspect https://github.com/{repo} before any cleanup.\n"
            f"Local project is intact at {dest}; re-run to retry."
        ) from exc

    # A settings-only retry must not inspect or publish later local work. An
    # unpublished resume, however, must test the actual committed snapshot again.
    # Check before creating/configuring anything and retain all partial state.
    try:
        if on_github:
            gitops.ensure_origin(dest, repo)
        pushed = on_github and gitops.remote_has_main(dest)
    except RuntimeError as exc:
        raise fail(
            f"{exc}\nCould not check remote main; refusing to push. "
            f"Inspect https://github.com/{repo}.\n"
            f"Local project is intact at {dest}; re-run to retry."
        ) from exc

    if not pushed:
        try:
            if publication_head is None:
                publication_head = gitops.bootstrap_head(dest)
                scaffold.smoke_test(dest, langs, typer.echo)
            gitops.require_bootstrap_head(dest, publication_head)
        except RuntimeError as exc:
            raise fail(
                f"{exc}\nBootstrap snapshot validation failed; this invocation did not "
                "attempt GitHub creation, configuration or push.\n"
                f"Local project is intact at {dest}; fix it and re-run."
            ) from exc

    try:
        configuration = github.prepare_configuration(owner, private, org=org)
    except github.GhError as exc:
        recovery = (
            "This invocation did not attempt GitHub creation or settings changes."
            if not on_github
            else "This invocation did not attempt GitHub settings changes."
        )
        raise fail(
            f"GitHub configuration preflight failed: {exc}\n{recovery} "
            "Local project is intact; fix the issue and re-run."
        ) from exc

    if not on_github:
        try:
            github.create_repo(repo, dest, name, private)
        except github.GhError as exc:
            raise fail(
                f"{exc}\nRemote creation was attempted; remote creation may have completed.\n"
                f"Inspect https://github.com/{repo} before any cleanup.\n"
                f"Local project is intact at {dest}; re-run to retry."
            ) from exc

    # Validate gh's freshly added origin too, including effective push URLs and
    # insteadOf rewrites. Creation does not establish the push destination.
    try:
        gitops.ensure_origin(dest, repo)
    except RuntimeError as exc:
        raise fail(
            f"{exc}\nOrigin validation failed; refusing configuration and push. "
            f"Inspect https://github.com/{repo}.\n"
            f"Local project is intact at {dest}; re-run to retry."
        ) from exc

    def publish_bootstrap() -> None:
        gitops.validate_origin(dest, repo)
        if publication_head is None:
            raise RuntimeError("no selected bootstrap snapshot")
        gitops.push_main(dest, publication_head)

    push = None if pushed else publish_bootstrap
    failed = github.configure_all(
        repo, private=private, configuration=configuration, push=push, log=typer.echo, org=org
    )
    typer.echo(f"\nhttps://github.com/{repo}\ncd {dest}")
    if failed:
        if not on_github:
            typer.echo(
                "The GitHub repository was created but setup did not finish. "
                "It remains available for retry; makeghrepo does not delete it."
            )
        typer.echo(f"{len(failed)} step(s) failed. Fix, then re-run: makeghrepo {name}")
        raise typer.Exit(2)
