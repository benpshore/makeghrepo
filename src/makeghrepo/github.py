"""GitHub setup via the ``gh`` CLI (which owns auth; we never touch tokens).

Every ``configure_*`` step is idempotent so ``makeghrepo configure OWNER/REPO``
can be re-run safely after a partial failure.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RULESET_NAME = "protect-main"
REQUIRED_CHECK = "ci"  # must match the job name in templates/*/.github/workflows/ci.yml
REQUIRED_SCOPES = {"repo", "workflow"}
OPTIONAL_SCOPES = {"project": "create and link a GitHub Project board"}

LABELS = (
    ("epic", "3E4B9E", "Large body of work tracked via sub-issues"),
    ("task", "C5DEF5", "A unit of work, usually a sub-issue of an epic"),
    ("dependencies", "0366D6", "Dependency updates"),
    ("python", "2B67C6", "Python dependency updates"),
    ("github-actions", "000000", "GitHub Actions updates"),
)


class GhError(RuntimeError):
    pass


@dataclass
class Gh:
    """Thin wrapper around the gh CLI. ``dry_run`` prints instead of executing mutations."""

    dry_run: bool = False
    log: Callable[[str], None] = print

    def run(self, *args: str, body: Any = None, mutate: bool = True) -> str:
        cmd = ["gh", *args]
        if body is not None:
            cmd += ["--input", "-"]
        if self.dry_run and mutate:
            suffix = f"  <<< {json.dumps(body)}" if body is not None else ""
            self.log("  [dry-run] " + " ".join(cmd) + suffix)
            return ""
        result = subprocess.run(
            cmd,
            input=json.dumps(body) if body is not None else None,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise GhError(f"{' '.join(cmd)}\n{result.stderr.strip() or result.stdout.strip()}")
        return result.stdout

    def api(self, method: str, path: str, body: Any = None) -> Any:
        out = self.run(
            "api",
            "-X",
            method,
            "-H",
            "Accept: application/vnd.github+json",
            path,
            body=body,
            mutate=method != "GET",
        )
        return json.loads(out) if out.strip() else None

    def current_user(self) -> str:
        return self.run("api", "user", "--jq", ".login", mutate=False).strip()

    def token_scopes(self) -> set[str]:
        out = self.run("api", "-i", "user", mutate=False)
        for line in out.splitlines():
            if line.lower().startswith("x-oauth-scopes:"):
                return {s.strip() for s in line.split(":", 1)[1].split(",") if s.strip()}
        return set()  # fine-grained tokens don't report scopes

    def repo_exists(self, full_name: str) -> bool:
        try:
            self.run("repo", "view", full_name, "--json", "name", mutate=False)
        except GhError:
            return False
        return True


@dataclass
class StepReport:
    ok: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    def step(self, name: str, fn: Callable[[], object], log: Callable[[str], None]) -> None:
        try:
            fn()
        except Exception as exc:  # report and keep going; the summary lists failures
            self.failed.append((name, str(exc)))
            log(f"  ✗ {name}: {exc}")
        else:
            self.ok.append(name)
            log(f"  ✓ {name}")


def create_repo(gh: Gh, full_name: str, source: Path, description: str, private: bool) -> None:
    gh.run(
        "repo",
        "create",
        full_name,
        "--private" if private else "--public",
        "--description",
        description,
        "--source",
        str(source),
        "--remote",
        "origin",
        "--push",
    )


def configure_repo_settings(gh: Gh, full_name: str) -> None:
    gh.api(
        "PATCH",
        f"repos/{full_name}",
        {
            "has_wiki": False,
            "has_issues": True,
            "has_projects": True,
            "allow_squash_merge": True,
            "allow_merge_commit": False,
            "allow_rebase_merge": False,
            "allow_auto_merge": True,
            "allow_update_branch": True,
            "delete_branch_on_merge": True,
            "squash_merge_commit_title": "PR_TITLE",
            "squash_merge_commit_message": "PR_BODY",
        },
    )


def configure_security(gh: Gh, full_name: str) -> None:
    gh.api("PUT", f"repos/{full_name}/vulnerability-alerts")
    gh.api("PUT", f"repos/{full_name}/automated-security-fixes")
    gh.api("PUT", f"repos/{full_name}/private-vulnerability-reporting")


def configure_secret_scanning(gh: Gh, full_name: str) -> None:
    # Free for public repos; private repos need GitHub Advanced Security.
    gh.api(
        "PATCH",
        f"repos/{full_name}",
        {
            "security_and_analysis": {
                "secret_scanning": {"status": "enabled"},
                "secret_scanning_push_protection": {"status": "enabled"},
            }
        },
    )


def ruleset_body() -> dict[str, Any]:
    """Protect the default branch: PRs only, CI must pass, no force-push/delete.

    Zero required approvals: on a solo repo you can't approve your own PR, so
    requiring one would block every merge. The PR + green CI gate is the point.
    """
    return {
        "name": RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "bypass_actors": [],
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
                    "required_status_checks": [{"context": REQUIRED_CHECK}],
                },
            },
        ],
    }


def configure_ruleset(gh: Gh, full_name: str) -> None:
    # In dry-run the repo may not exist yet, so assume there's nothing to update.
    existing = [] if gh.dry_run else gh.api("GET", f"repos/{full_name}/rulesets") or []
    match = next((r for r in existing if r.get("name") == RULESET_NAME), None)
    if match:
        gh.api("PUT", f"repos/{full_name}/rulesets/{match['id']}", ruleset_body())
    else:
        gh.api("POST", f"repos/{full_name}/rulesets", ruleset_body())


def configure_labels(gh: Gh, full_name: str) -> None:
    for name, color, description in LABELS:
        gh.run(
            "label",
            "create",
            name,
            "--repo",
            full_name,
            "--color",
            color,
            "--description",
            description,
            "--force",
        )


def mute_notifications(gh: Gh, full_name: str) -> None:
    """Set the repo to 'Ignore' so the owner gets no watch notifications."""
    gh.api("PUT", f"repos/{full_name}/subscription", {"subscribed": False, "ignored": True})


def configure_project(gh: Gh, full_name: str) -> None:
    owner, name = full_name.split("/", 1)
    if gh.dry_run:
        gh.run("project", "create", "--owner", owner, "--title", name, "--format", "json")
        gh.run("project", "link", "<number>", "--owner", owner, "--repo", full_name)
        return
    listing = json.loads(
        gh.run("project", "list", "--owner", owner, "--format", "json", mutate=False) or "{}"
    )
    number = next(
        (p["number"] for p in listing.get("projects", []) if p.get("title") == name), None
    )
    if number is None:
        created = gh.run("project", "create", "--owner", owner, "--title", name, "--format", "json")
        number = json.loads(created)["number"]
    gh.run("project", "link", str(number), "--owner", owner, "--repo", full_name)


def configure_all(gh: Gh, full_name: str, *, project: bool = True) -> StepReport:
    report = StepReport()
    steps: list[tuple[str, Callable[[], object]]] = [
        (
            "repo settings (squash-only, auto-delete branches)",
            lambda: configure_repo_settings(gh, full_name),
        ),
        (
            "dependabot alerts + security fixes + private vuln reporting",
            lambda: configure_security(gh, full_name),
        ),
        ("secret scanning + push protection", lambda: configure_secret_scanning(gh, full_name)),
        (f"ruleset '{RULESET_NAME}' on default branch", lambda: configure_ruleset(gh, full_name)),
        ("labels", lambda: configure_labels(gh, full_name)),
        ("mute notifications (watch: ignore)", lambda: mute_notifications(gh, full_name)),
    ]
    if project:
        steps.append(("project board", lambda: configure_project(gh, full_name)))
    for name, fn in steps:
        report.step(name, fn, gh.log)
    return report
