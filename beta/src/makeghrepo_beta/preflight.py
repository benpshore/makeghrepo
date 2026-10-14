"""Explicit read-only observations, never authorization for future mutations."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from makeghrepo import names
from makeghrepo_beta.plan import BootstrapPlan, destination_state


def gh_read(endpoint: str) -> object:
    # Only callers below choose endpoints; never accept arbitrary API paths,
    # methods, headers, shell text or credential values from a saved plan.
    result = subprocess.run(
        ["gh", "api", "--hostname", "github.com", "--method", "GET", endpoint],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        # Backend errors may include private URLs or authentication details.
        raise RuntimeError("GitHub metadata unavailable; check gh authentication and connectivity")
    if len(result.stdout) > 1_048_576:
        raise ValueError("GitHub metadata exceeded the response limit")
    return json.loads(result.stdout)


def inspect(plan: BootstrapPlan, read: Callable[[str], object] | None = None) -> dict[str, object]:
    read = gh_read if read is None else read
    observations = []
    observed_at = datetime.now(UTC).isoformat()

    def note(capability, status, detail, source):
        observations.append(
            dict(capability=capability, status=status, detail=detail, source=source)
        )

    def query(endpoint):
        try:
            return read(endpoint)
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
            return None

    user = query("user")
    login = None
    if isinstance(user, dict) and type(user.get("id")) is int and user["id"] > 0:
        try:
            login = names.validate_owner(user["login"])
        except (KeyError, TypeError, ValueError):
            pass
    if login is None:
        note("identity", "unknown", "Authenticated identity was not verified", "GET user")
    else:
        note("identity", "observed", f"Authenticated account: {login}", "GET user")
    resolved = replace(plan, owner=plan.owner or login)
    if resolved.owner:
        owner = resolved.owner
        if login and owner.casefold() != login.casefold():
            note("owner_policy", "unknown", "Organization or other-owner policy is not verified", "input")
        endpoint = f"repos/{owner}/{plan.name}"
        repo = query(endpoint)
        expected = f"{owner}/{plan.name}"
        if (
            isinstance(repo, dict)
            and isinstance(repo.get("full_name"), str)
            and repo["full_name"].casefold() == expected.casefold()
            and type(repo.get("id")) is int
            and repo["id"] > 0
        ):
            note("target", "observed", "Repository is visible; creation is not available", f"GET {endpoint}")
            permissions = repo.get("permissions")
            if isinstance(permissions, dict) and type(permissions.get("admin")) is bool:
                note(
                    "existing_repository_admin", "observed" if permissions["admin"] else "denied",
                    "Reported permission applies only to this existing repository", f"GET {endpoint}",
                )
        else:
            note("target", "unknown", "Not observed; absence and name availability are not proven", f"GET {endpoint}")
        destination = resolved.root / owner / ("private" if plan.private else "public") / plan.name
        try:
            destination_state(destination)
        except (OSError, ValueError):
            note("destination", "denied", "Destination is inaccessible, unsafe or nonempty", "filesystem")
        else:
            note("destination", "observed", "Destination is currently absent or empty", "filesystem")
    for capability in ("repository_creation", "ruleset_support", "actions_runners_and_quota"):
        note(capability, "unknown", "Read-only metadata does not establish this capability", "not verified")
    note("live_execution", "unsupported", "Beta live bootstrap remains unavailable", "beta policy")
    report = resolved.as_dict()
    report.update(operation="bootstrap-preflight", observed_at=observed_at, observations=observations)
    report["limits"] = [
        "Read-only observations may become stale and never authorize a later mutation.",
        "No files, repositories, credentials, workflows or settings were created or changed.",
        "A failed lookup is not proof a repository is absent. No name is reserved.",
        "Live bootstrap requires a reviewed no-bypass publication and execution strategy.",
    ]
    return report
