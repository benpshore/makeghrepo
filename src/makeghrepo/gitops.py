"""Local git operations by shelling out to the ``git`` CLI (standard library only)."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

MARKER = "makeghrepo.json"  # lives inside .git, so it is never committed or pushed


class GitError(RuntimeError):
    """A ``git`` invocation failed or could not start.

    ``returncode`` is git's exit status, or None when git never ran (not installed).
    """

    def __init__(self, args: tuple[str, ...], returncode: int | None, stderr: str = "") -> None:
        self.args_ = args
        self.returncode = returncode
        self.stderr = stderr.strip()
        detail = f": {self.stderr}" if self.stderr else ""
        status = "could not run" if returncode is None else f"exited with {returncode}"
        super().__init__(f"git {' '.join(args)} {status}{detail}")


def _git(
    *args: str, cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` (in ``cwd`` when given) and return the finished process.

    Raises GitError on a non-zero exit when ``check`` is true, and always when git
    cannot be started. Output is decoded as UTF-8 with replacement so a stray byte
    in a path or message can never raise.
    """
    command = ["git", *(["-C", str(cwd)] if cwd is not None else []), *args]
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, check=False,
        )  # fmt: skip
    except OSError as exc:  # git missing or not executable
        raise GitError(args, None, str(exc)) from exc
    if check and proc.returncode != 0:
        raise GitError(args, proc.returncode, proc.stderr)
    return proc


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

    def get(key: str) -> str:
        # Exit 1 just means "not set". Any other failure (git missing, unreadable
        # config) is also treated as unset: this only picks a display name.
        try:
            proc = _git("config", "--global", "--get", key, check=False)
        except GitError:
            return ""
        return proc.stdout.strip() if proc.returncode == 0 else ""

    return get("user.name"), get("user.email")


def init(path: Path) -> None:
    _git("init", "--quiet", "--initial-branch=main", str(path))


def ensure_identity(path: Path, name: str, email: str) -> None:
    """Set commit identity on this repo only, if none is set at any config level.

    A fresh host or CI runner often has no ``user.name``/``user.email`` configured
    anywhere, and ``git commit`` refuses to run without one. Ask ``git config``
    itself so the check matches what ``git commit`` will see, including
    ``GIT_CONFIG_*`` env vars. Never touch the user's global config: a plain
    ``git config <key> <value>`` writes to this repo's own ``.git/config``.
    """
    for key, value in (("user.name", name), ("user.email", email)):
        if _git("config", "--get", key, cwd=path, check=False).returncode != 0:
            _git("config", key, value, cwd=path)


def has_commits(path: Path) -> bool:
    return _git("rev-parse", "--verify", "--quiet", "HEAD", cwd=path, check=False).returncode == 0


def commit_all(path: Path, message: str = "setup") -> str:
    """Commit the staged tree without silently accepting hook changes."""
    _git("add", "--all", cwd=path)
    # -z: NUL-separated and unquoted, so unusual file names are checked as written.
    staged = _git("diff", "--cached", "--name-only", "-z", cwd=path).stdout
    junk = junk_files([name for name in staged.split("\0") if name])
    if junk:
        raise RuntimeError(f"refusing to commit junk or secrets: {', '.join(junk)}")
    staged_tree = _git("write-tree", cwd=path).stdout.strip()
    # The git CLI honors hooks and commit signing.
    _git("commit", "-m", message, cwd=path)
    committed = _git("rev-parse", "--verify", "HEAD", cwd=path).stdout.strip()
    if _git("rev-parse", f"{committed}^{{tree}}", cwd=path).stdout.strip() != staged_tree:
        raise RuntimeError(
            "commit changed the staged tree; the local commit is retained, "
            "re-run to validate it before publication"
        )
    return committed


def bootstrap_head(path: Path) -> str:
    """Identify a clean checked-out main, including staged and untracked changes.

    The caller checks this both before and after smoke tests and again before
    publication. The pushed source is still the immutable commit ID: a change
    after the final local check must never substitute unvalidated content.
    """
    branch = _git("symbolic-ref", "--quiet", "HEAD", cwd=path, check=False)
    if branch.returncode or branch.stdout.strip() != "refs/heads/main":
        raise RuntimeError("bootstrap publication requires main to be checked out")
    # Replacements can make HEAD/status describe a different tree from the
    # original objects a push transfers. Ask Git for the effective namespace
    # (including GIT_REPLACE_REF_BASE), without changing config or refs.
    if _git("replace", "-l", cwd=path).stdout:
        raise RuntimeError("bootstrap publication does not support Git replacement objects")
    head = _git("rev-parse", "--verify", "refs/heads/main^{commit}", cwd=path).stdout.strip()
    # Git status trusts these index flags and can otherwise hide modified check
    # inputs. Do not silently change the user's flags to make the snapshot pass.
    indexed = _git("ls-files", "--cached", "--stage", "-v", "-z", cwd=path).stdout
    entries = [entry.split(" ", 2) for entry in indexed.split("\0") if entry]
    if any(tag == "S" or tag.islower() for tag, _, _ in entries):
        raise RuntimeError(
            "bootstrap publication requires tracked files without assume-unchanged "
            "or skip-worktree flags"
        )
    if any(mode == "160000" for _, mode, _ in entries):
        raise RuntimeError("bootstrap snapshot validation does not support Git submodules")
    if _git(
        "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none", cwd=path
    ).stdout:
        raise RuntimeError(
            "bootstrap publication requires a clean worktree, index and untracked files"
        )
    if (
        _git("rev-parse", "--verify", "HEAD", cwd=path).stdout.strip() != head
        or _git("symbolic-ref", "--quiet", "HEAD", cwd=path, check=False).stdout.strip()
        != "refs/heads/main"
    ):
        raise RuntimeError("main changed while checking the bootstrap snapshot")
    return head


def require_bootstrap_head(path: Path, expected: str) -> None:
    if bootstrap_head(path) != expected:
        raise RuntimeError(
            "main changed after selecting the publication snapshot; re-run to check it"
        )


def push_main(path: Path, publication_head: str) -> None:
    """Create remote main from the selected commit, never advance an existing ref.

    An explicitly empty expected value prevents replacing an existing main,
    including one created between ls-remote and this call. Git also reports
    success if another actor already published the identical commit; this does
    not prove which invocation created it. Server protections remain in effect.
    Do not replace the empty lease with a tracking ref or an unrestricted force.
    """
    require_bootstrap_head(path, publication_head)
    try:
        _git(
            "push", "--force-with-lease=refs/heads/main:", "origin",
            f"{publication_head}:refs/heads/main", cwd=path,
        )  # fmt: skip
    except GitError as exc:
        try:
            observed = remote_main(path)
            state = (
                f"remote main is currently observed at {observed}"
                if observed is not None
                else "remote main is currently absent"
            )
        except RuntimeError:
            state = "remote main could not be read; publication may have completed"
        raise RuntimeError(f"{exc}; {state}. Inspect the remote before retrying") from exc
    try:
        observed = remote_main(path)
    except RuntimeError as exc:
        raise RuntimeError(
            f"push returned but remote main readback failed: {exc}; "
            "publication may have completed, inspect the remote before retrying"
        ) from exc
    if observed != publication_head:
        raise RuntimeError(
            "push returned but remote main no longer matches the selected snapshot; "
            "publication may have completed, inspect the remote before retrying"
        )
    try:
        _git("branch", "--set-upstream-to=origin/main", "main", cwd=path)
    except GitError as exc:
        raise RuntimeError(
            f"remote main was verified at {publication_head}; "
            f"only local upstream setup failed: {exc}"
        ) from exc


def validate_origin(path: Path, full_name: str) -> None:
    """Refuse a resume whose effective fetch or push URLs target another repo.

    A missing origin is normal after a failed create; ensure_origin repairs it.
    Ask git for expanded URLs so pushurl and insteadOf rewrites are checked too.
    Never print an unexpected URL: it could contain embedded credentials.
    """
    if "origin" not in _git("remote", cwd=path).stdout.split():
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
        urls = _git("remote", "get-url", *options, "origin", cwd=path).stdout.splitlines()
        if not urls or any(url.rstrip("/").casefold() not in allowed for url in urls):
            raise RuntimeError(
                f"origin does not match {full_name}; restore its GitHub fetch and push URLs "
                "before resuming"
            )


def ensure_origin(path: Path, full_name: str) -> None:
    """Repair only a missing origin on a marker-validated bootstrap resume.

    Creation can succeed remotely before gh adds the local remote. Never replace
    an existing origin, and check effective URLs (including Git rewrites) after
    adding it, before a remote lookup or any push is permitted.
    """
    if "origin" not in _git("remote", cwd=path).stdout.splitlines():
        _git("remote", "add", "origin", f"https://github.com/{full_name}.git", cwd=path)
    validate_origin(path, full_name)


def remote_main(path: Path) -> str | None:
    """Return the observed main commit, or None only for a successful empty read."""
    result = _git("ls-remote", "--heads", "origin", "refs/heads/main", cwd=path).stdout.strip()
    if not result:
        return None
    fields = result.split()
    if (
        len(fields) != 2
        or fields[1] != "refs/heads/main"
        or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", fields[0])
    ):
        raise RuntimeError("remote main lookup returned an unexpected response")
    return fields[0]


def remote_has_main(path: Path) -> bool:
    """Check main without treating a failed lookup as an empty remote.

    A successful empty response permits the bootstrap push. Missing origins,
    authentication failures and unreachable remotes must stop a configuration
    retry, otherwise it could publish subsequent local commits.
    """
    return remote_main(path) is not None
