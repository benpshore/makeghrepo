"""GitHub setup via the ``gh`` CLI (which owns auth; we never touch tokens).

Every step is idempotent, so ``makeghrepo configure OWNER/REPO`` can be re-run
after a partial failure.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

RULESET_NAME = "protect-main"
REQUIRED_CHECK = "ci"  # must match the job name in templates/*/.github/workflows/ci.yml
GITHUB_ACTIONS_APP_ID = 15368  # only GitHub Actions may satisfy the required check
LABELS = {"epic": "3E4B9E", "task": "C5DEF5"}


class GhError(RuntimeError):
    pass


def gh(*args: str, body: Any = None) -> str:
    cmd = ["gh", *args] + (["--input", "-"] if body is not None else [])
    result = subprocess.run(
        cmd, input=None if body is None else json.dumps(body), capture_output=True, text=True
    )
    if result.returncode != 0:
        raise GhError(f"{' '.join(cmd)}\n{(result.stderr or result.stdout).strip()}")
    return result.stdout


def api(method: str, path: str, body: Any = None) -> Any:
    out = gh("api", "-X", method, path, body=body)
    return json.loads(out) if out.strip() else None


def current_user() -> str:
    return gh("api", "user", "--jq", ".login").strip()


def repo_exists(full_name: str) -> bool:
    try:
        gh("repo", "view", full_name, "--json", "name")
    except GhError as exc:
        if "Could not resolve to a Repository" in str(exc):
            return False
        raise  # network/auth/rate-limit: don't treat as "name is free"
    return True


def is_private(repo: str) -> bool:
    return bool(api("GET", f"repos/{repo}")["private"])


def create_repo(repo: str, source: Path, description: str, private: bool) -> None:
    """Create an empty repo and add it as ``origin``. The push happens later, in
    configure_all, once push protection is on."""
    gh("repo", "create", repo, "--private" if private else "--public",
       "--description", description, "--source", str(source), "--remote", "origin")  # fmt: skip


def ruleset_body() -> dict[str, Any]:
    """PRs only, CI must pass, no force-push/delete. Zero approvals: you can't
    approve your own PR on a solo repo."""
    return {
        "name": RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "required_linear_history"},
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": 0,
                    "dismiss_stale_reviews_on_push": True,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_review_thread_resolution": True,
                    "allowed_merge_methods": ["squash"],
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": False,
                    "required_status_checks": [
                        {"context": REQUIRED_CHECK, "integration_id": GITHUB_ACTIONS_APP_ID}
                    ],
                },
            },
        ],
    }


def configure_ruleset(repo: str) -> None:
    existing = api("GET", f"repos/{repo}/rulesets?includes_parents=false") or []
    match = next((r["id"] for r in existing if r.get("name") == RULESET_NAME), None)
    if match:
        api("PUT", f"repos/{repo}/rulesets/{match}", ruleset_body())
    else:
        api("POST", f"repos/{repo}/rulesets", ruleset_body())


FIND_PROJECT_QUERY = """
query($owner: String!, $title: String!) {
  user(login: $owner) {
    projectsV2(first: 100, query: $title) {
      nodes { number title }
    }
  }
}
"""


def _find_project_by_title(owner: str, title: str) -> int:
    """GraphQL text search: cheap, but a substring match, and capped at one page.

    If the page comes back full (100) with no exact title match, an existing
    project could be sitting on a second page — fall back to the old full
    scan rather than risk creating a duplicate board.
    """
    listing = gh("api", "graphql", "-f", f"query={FIND_PROJECT_QUERY}", "-f", f"owner={owner}",
                 "-f", f"title={title}")  # fmt: skip
    nodes = json.loads(listing)["data"]["user"]["projectsV2"]["nodes"]
    number = next((p["number"] for p in nodes if p["title"] == title), 0)
    if number or len(nodes) < 100:
        return number
    full = gh("project", "list", "--owner", owner, "--closed", "--limit", "1000",
              "--format", "json")  # fmt: skip
    return next((p["number"] for p in json.loads(full)["projects"] if p["title"] == title), 0)


def configure_project(repo: str) -> None:
    """Link the repo to a same-named project, creating one if it doesn't exist."""
    owner, title = repo.split("/")
    number = _find_project_by_title(owner, title)
    if not number:
        created = gh("project", "create", "--owner", owner, "--title", title, "--format", "json")
        number = json.loads(created)["number"]
    gh("project", "link", str(number), "--owner", owner, "--repo", repo)


def disable_actions(repo: str) -> None:
    """Turn off GitHub Actions entirely for this repo.

    Private repos here have no Actions minutes to spare, and `ci.yml`/
    `release.yml`/Dependabot-triggered runs would otherwise fire on the very
    first push regardless of branch protection (which Free doesn't offer on
    private repos anyway) — so this has to happen before that push, not after.
    """
    api("PUT", f"repos/{repo}/actions/permissions", {"enabled": False})


def settings_body(private: bool) -> dict[str, Any]:
    body: dict[str, Any] = {
        "has_wiki": False,
        "allow_merge_commit": False,
        "allow_rebase_merge": False,
        # Requested either way, but confirmed live (GET after PATCH) that this
        # silently stays false on a Free-plan private repo — there's no
        # ruleset/required-check there for auto-merge to wait on, so GitHub
        # appears to just drop it rather than error. Not worth branching on:
        # it's harmless to keep requesting, and it does apply on public repos.
        "allow_auto_merge": True,
        "allow_update_branch": True,
        "delete_branch_on_merge": True,
        "squash_merge_commit_title": "PR_TITLE",
        "squash_merge_commit_message": "PR_BODY",
    }
    if not private:  # free on public repos; private needs Advanced Security
        body["security_and_analysis"] = {
            "secret_scanning": {"status": "enabled"},
            "secret_scanning_push_protection": {"status": "enabled"},
        }
    return body


def configure_all(
    repo: str,
    *,
    private: bool,
    project: bool = True,
    push: Callable[[], object] | None = None,
    log: Callable[[str], None] = print,
) -> list[str]:
    """Apply every setting; return the names of failed steps (the rest still run).

    Three phases, so independent configuration doesn't wait on itself: (1)
    repo settings, serial; (2) every independent step — including the push
    itself — run concurrently; (3) the ruleset, which needs the pushed `main`
    to protect, so it only runs once the push (if any) has actually succeeded.

    Publishing is gated on the relevant protection step: public repo settings
    enable secret-scanning push protection; private repos must have Actions
    disabled. If that prerequisite fails, independent settings still run, but
    the push and the ruleset for that unpushed main are skipped. A settings-only
    retry (push=None) can still repair protection on an existing main.
    """
    failed: list[str] = []

    def run(fn: Callable[[], object]) -> Exception | None:
        try:
            fn()
        except Exception as exc:  # report and keep going
            return exc
        return None

    def report(name: str, err: Exception | None) -> None:
        log(f"  ✗ {name}: {err}" if err else f"  ✓ {name}")
        if err:
            failed.append(name)

    if private:
        # GitHub Free: no rulesets, secret scanning or code scanning on private repos,
        # and Actions get disabled below rather than left running with nothing to
        # gate — the local smoke test is the only gate left for these repos.
        log("  - private repo: skipping ruleset, secret scanning, vuln reporting (need paid plan)")

    settings_err = run(lambda: api("PATCH", f"repos/{repo}", settings_body(private)))
    report("repo settings", settings_err)
    actions_disabled = True  # only meaningful, and only checked below, when private
    if private:
        actions_err = run(lambda: disable_actions(repo))
        report("disable actions", actions_err)
        actions_disabled = actions_err is None

    fanout: list[tuple[str, Callable[[], object]]] = [
        # One task, alerts before fixes: enabling automated fixes while alerts
        # are still off can 422. They're independent of everything else here,
        # just not of each other.
        ("dependabot alerts + security fixes", lambda: (
            api("PUT", f"repos/{repo}/vulnerability-alerts"),
            api("PUT", f"repos/{repo}/automated-security-fixes"),
        )),
    ]  # fmt: skip
    if not private:
        pvr = f"repos/{repo}/private-vulnerability-reporting"
        fanout.append(("private vulnerability reporting", lambda: api("PUT", pvr)))
    fanout += [
        ("labels: " + ", ".join(LABELS), lambda: [
            gh("label", "create", name, "--repo", repo, "--color", color, "--force")
            for name, color in LABELS.items()
        ]),
        ("mute notifications (watch: ignore)",
         lambda: api("PUT", f"repos/{repo}/subscription", {"subscribed": False, "ignored": True})),
    ]  # fmt: skip
    if project:
        fanout.append(("project board", lambda: configure_project(repo)))
    push_blocked = bool(push) and (
        not actions_disabled or (not private and settings_err is not None)
    )
    if push and not actions_disabled:
        # Refuse to push rather than risk a run: disabling Actions is the one
        # thing standing between a private repo and burning its own minutes.
        log("  - push main: skipped (couldn't disable Actions; refusing to risk a run)")
        failed.append("push main")
    elif push_blocked:
        log("  - push main: skipped (couldn't enable push protection; refusing to publish)")
        failed.append("push main")
    elif push:
        fanout.append(("push main", push))

    with ThreadPoolExecutor(max_workers=min(4, len(fanout))) as pool:
        errors = list(pool.map(lambda item: run(item[1]), fanout))  # declared order, not completion
    for (name, _), err in zip(fanout, errors, strict=True):
        report(name, err)

    # Identity, not the step's display name, so renaming a step (as this diff
    # already does for the dependabot pair) can't silently break this check.
    push_failed = any(
        fn is push and err is not None for (_, fn), err in zip(fanout, errors, strict=True)
    )
    if not private and not push_blocked and not push_failed:
        report(f"ruleset '{RULESET_NAME}'", run(lambda: configure_ruleset(repo)))

    return failed
