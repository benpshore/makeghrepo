"""GitHub setup via the ``gh`` CLI (which owns auth; we never touch tokens).

Every step is idempotent, so ``makeghrepo configure OWNER/REPO`` can be re-run
after a partial failure.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
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


def configure_project(repo: str) -> None:
    owner, title = repo.split("/")
    listing = gh("project", "list", "--owner", owner, "--closed", "--limit", "1000",
                 "--format", "json")  # fmt: skip
    number = next((p["number"] for p in json.loads(listing)["projects"] if p["title"] == title), 0)
    if not number:
        created = gh("project", "create", "--owner", owner, "--title", title, "--format", "json")
        number = json.loads(created)["number"]
    gh("project", "link", str(number), "--owner", owner, "--repo", repo)


def settings_body(private: bool) -> dict[str, Any]:
    body: dict[str, Any] = {
        "has_wiki": False,
        "allow_merge_commit": False,
        "allow_rebase_merge": False,
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

    ``push`` runs after secret-scanning push protection is on (so it covers the
    first push) and before the ruleset (which would block pushing to main).
    """
    steps: list[tuple[str, Callable[[], object]]] = [
        ("repo settings", lambda: api("PATCH", f"repos/{repo}", settings_body(private))),
        ("dependabot alerts", lambda: api("PUT", f"repos/{repo}/vulnerability-alerts")),
        ("dependabot security fixes", lambda: api("PUT", f"repos/{repo}/automated-security-fixes")),
    ]  # fmt: skip
    if not private:
        pvr = f"repos/{repo}/private-vulnerability-reporting"
        steps.append(("private vulnerability reporting", lambda: api("PUT", pvr)))
    if push:
        steps.append(("push main", push))
    if private:
        # GitHub Free: no rulesets, secret scanning or code scanning on private repos.
        # CI still runs but can't block merges; the local smoke test is the gate.
        log("  - private repo: skipping ruleset, secret scanning, vuln reporting (need paid plan)")
    else:
        steps.append((f"ruleset '{RULESET_NAME}'", lambda: configure_ruleset(repo)))
    steps += [
        ("labels: " + ", ".join(LABELS), lambda: [
            gh("label", "create", name, "--repo", repo, "--color", color, "--force")
            for name, color in LABELS.items()
        ]),
        ("mute notifications (watch: ignore)",
         lambda: api("PUT", f"repos/{repo}/subscription", {"subscribed": False, "ignored": True})),
    ]  # fmt: skip
    if project:
        steps.append(("project board", lambda: configure_project(repo)))

    failed: list[str] = []
    for name, fn in steps:
        try:
            fn()
        except Exception as exc:  # report and keep going
            log(f"  ✗ {name}: {exc}")
            failed.append(name)
            if fn is push:
                break  # nothing after this makes sense without the code on GitHub
        else:
            log(f"  ✓ {name}")
    return failed
