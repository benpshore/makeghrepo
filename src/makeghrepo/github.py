"""GitHub setup via the ``gh`` CLI (which owns auth; we never touch tokens).

Every step is idempotent, so ``makeghrepo NAME`` can be re-run
after a partial failure.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RULESET_NAME = "protect-main"
REVIEW_RULESET_NAME = "require-pr-and-ci"
REQUIRED_CHECK = "ci"  # must match the job name in templates/*/.github/workflows/ci.yml
GITHUB_ACTIONS_APP_ID = 15368  # only GitHub Actions may satisfy the required check
LABELS = {"epic": "3E4B9E", "task": "C5DEF5"}
MAIN_REF = "refs/heads/main"


class GhError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConfigurationPlan:
    """Settings payloads prepared before creating the repository resource.

    Repository-scoped API calls still need the created repo. This plan only
    captures request data and verifies the public-repo owner identity early.
    """

    owner: str
    owner_id: int | None
    private: bool
    settings: dict[str, Any]
    rulesets: tuple[dict[str, Any], ...]


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
    """Create an empty repo and add it as ``origin``.

    Repo-scoped settings cannot be applied until GitHub has created this resource;
    configure_all installs the required protections before the initial main push.
    """
    gh("repo", "create", repo, "--private" if private else "--public",
       "--description", description, "--source", str(source), "--remote", "origin")  # fmt: skip


def ruleset_body() -> dict[str, Any]:
    """History protections apply to everyone, including the owner."""
    return {
        "name": RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "required_linear_history"},
        ],
    }


def review_ruleset_body(owner_id: int) -> dict[str, Any]:
    """Only the personal repository owner may push without a PR or passing CI."""
    return {
        **ruleset_body(),
        "name": REVIEW_RULESET_NAME,
        "bypass_actors": [{"actor_type": "User", "actor_id": owner_id, "bypass_mode": "always"}],
        "rules": [
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
                    # Retain the strict live gate on configure reruns (#126).
                    "strict_required_status_checks_policy": True,
                    "do_not_enforce_on_create": False,
                    "required_status_checks": [
                        {"context": REQUIRED_CHECK, "integration_id": GITHUB_ACTIONS_APP_ID}
                    ],
                },
            },
        ],
    }


def _with_main_scope(body: dict[str, Any]) -> dict[str, Any]:
    """Keep default-branch rules and explicitly protect the bootstrap branch."""
    result = deepcopy(body)
    include = result["conditions"]["ref_name"]["include"]
    if MAIN_REF not in include:
        include.append(MAIN_REF)
    return result


def _ruleset_bodies(owner_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    history, review = ruleset_body(), review_ruleset_body(owner_id)
    return _with_main_scope(history), _with_main_scope(review)


def _scope_matches(saved: Any, desired: dict[str, Any]) -> bool:
    """Accept the previous default-only scope for safe additive migration."""
    if saved == desired:
        return True
    previous = deepcopy(desired)
    include = previous["ref_name"]["include"]
    if MAIN_REF not in include:
        return False
    include.remove(MAIN_REF)
    return saved == previous


def prepare_configuration(owner_login: str, private: bool) -> ConfigurationPlan:
    """Prepare and validate payloads before creating the GitHub repository.

    Public rulesets need the personal account ID as a bypass actor. Fetch and
    validate it now so identity or serialization errors cannot strand a newly
    created, unprotected repository. The later repo-scoped call verifies that
    the created repo has this same owner before installing those rules.
    """
    owner_id = None
    rulesets: tuple[dict[str, Any], ...] = ()
    if not private:
        try:
            user = api("GET", "user")
        except (GhError, ValueError) as exc:
            raise GhError(
                f"could not verify the public repository owner before creation: {exc}"
            ) from exc
        candidate = user.get("id") if isinstance(user, dict) else None
        login = user.get("login") if isinstance(user, dict) else None
        if (
            not isinstance(candidate, int)
            or isinstance(candidate, bool)
            or not 0 < candidate < 2**64
            or not isinstance(login, str)
            or login.casefold() != owner_login.casefold()
        ):
            raise GhError("owner push bypass requires a verified matching personal account ID")
        owner_id = candidate
        rulesets = _ruleset_bodies(owner_id)

    settings = settings_body(private)
    try:
        # Validate the exact payload shapes that will later be sent to GitHub.
        json.dumps({"settings": settings, "rulesets": rulesets})
    except (TypeError, ValueError) as exc:
        raise GhError("could not prepare GitHub settings before repository creation") from exc
    return ConfigurationPlan(owner_login, owner_id, private, settings, rulesets)


def configure_ruleset(
    repo: str, configuration: ConfigurationPlan | None = None
) -> None:
    # The repository exists now; confirm its owner is a personal account before
    # installing a bypass. The CLI also compares it with the precreation plan.
    owner = api("GET", f"repos/{repo}")["owner"]
    owner_id = owner.get("id")
    repo_owner = repo.split("/", 1)[0]
    owner_login = owner.get("login")
    if (
        owner.get("type") != "User"
        or not isinstance(owner_id, int)
        or isinstance(owner_id, bool)
        or not 0 < owner_id < 2**64
        or not isinstance(owner_login, str)
        or owner_login.casefold() != repo_owner.casefold()
        or (
            configuration is not None
            and (
                owner_id != configuration.owner_id
                or owner.get("login", "").casefold() != configuration.owner.casefold()
                or configuration.private
            )
        )
    ):
        raise GhError("owner push bypass requires a personal repository with a verified owner ID")

    bodies = (
        deepcopy(configuration.rulesets)
        if configuration is not None
        else list(_ruleset_bodies(owner_id))
    )
    existing = []
    page = 1
    while True:
        batch = api("GET", f"repos/{repo}/rulesets?includes_parents=false&per_page=100&page={page}")
        existing.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    ids: dict[str, int] = {}
    saved_rules: dict[str, dict[str, Any]] = {}
    # Read and validate both full bodies before writing anything. Lists omit
    # rules/bypasses. Refuse custom scopes and conflicting gate policies rather
    # than silently granting a wider bypass or dropping an existing requirement.
    for body in bodies:
        name = body["name"]
        matches = [r for r in existing if r.get("name") == name]
        if len(matches) > 1:
            raise GhError(f"duplicate ruleset {name!r}; reconcile it manually before configuring")
        rules: dict[str, Any] = {}
        if matches:
            ids[name] = matches[0]["id"]
            saved = api("GET", f"repos/{repo}/rulesets/{ids[name]}")
            if (
                any(saved.get(k) != body[k] for k in ("target", "enforcement"))
                or not _scope_matches(saved.get("conditions"), body["conditions"])
                or saved.get("bypass_actors") not in ([], body["bypass_actors"])
            ):
                raise GhError(f"customized ruleset {name!r}; review its scope/bypasses manually")
            for rule in saved["rules"]:
                if rule["type"] in rules:
                    raise GhError(f"duplicate rule in {name!r}; reconcile it manually")
                rules[rule["type"]] = rule
        saved_rules[name] = rules

    history, review = bodies
    legacy = saved_rules[RULESET_NAME]
    gates = saved_rules[REVIEW_RULESET_NAME]
    gate_types = {rule["type"] for rule in review["rules"]}
    if gates.keys() - gate_types:
        raise GhError(f"customized rules in {REVIEW_RULESET_NAME!r}; cannot grant owner bypass")
    for index, default in enumerate(review["rules"]):
        kind = default["type"]
        if kind in legacy and kind in gates and legacy[kind] != gates[kind]:
            raise GhError(f"conflicting {kind} rules; reconcile them manually before configuring")
        # Preserve extra checks/review parameters, including fields GitHub adds.
        review["rules"][index] = gates.get(kind, legacy.get(kind, default))
    history["rules"] = list(
        (
            {r["type"]: r for r in history["rules"]}
            | {kind: rule for kind, rule in legacy.items() if kind not in gate_types}
        ).values()
    )

    # Install the replacement gate before removing the legacy gate. If either
    # request fails, an existing protected repo retains its PR/CI requirements.
    for body in (review, history):
        match = ids.get(body["name"])
        if match:
            api("PUT", f"repos/{repo}/rulesets/{match}", body)
        else:
            api("POST", f"repos/{repo}/rulesets", body)


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
        "allow_squash_merge": True,
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
    configuration: ConfigurationPlan | None = None,
    project: bool = True,
    push: Callable[[], object] | None = None,
    log: Callable[[str], None] = print,
) -> list[str]:
    """Apply repo-scoped settings and return failed steps for a safe retry.

    The CLI prepares payloads before creation. GitHub settings, Actions
    permissions, rulesets, labels, Dependabot, and Project linking are repo-scoped
    and require the created repository. Public rulesets and secret-scanning push
    protection (or private Actions disablement) must settle before an initial
    push. Remaining independent steps then run concurrently.

    Creation and configuration are separate GitHub operations, not an atomic
    transaction. On a post-creation failure the empty/partially configured repo
    remains for an idempotent retry; it is never deleted as rollback. If a
    prerequisite fails, independent settings still run, but main is not pushed.
    A settings-only retry (push=None) can still repair protections on an existing
    main.
    """
    if configuration is None:
        configuration = prepare_configuration(repo.split("/", 1)[0], private)
    if (
        configuration.private != private
        or configuration.owner.casefold() != repo.split("/", 1)[0].casefold()
    ):
        raise GhError("prepared GitHub settings do not match repository owner or visibility")

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

    settings_err = run(lambda: api("PATCH", f"repos/{repo}", configuration.settings))
    report("repo settings", settings_err)
    actions_disabled = True  # only meaningful, and only checked below, when private
    if private:
        actions_err = run(lambda: disable_actions(repo))
        report("disable actions", actions_err)
        actions_disabled = actions_err is None

    ruleset_err = None
    # Install default-branch rules before the first push. Existing-main retries
    # still repair the ruleset even if the settings PATCH failed.
    if not private and (push is None or settings_err is None):
        ruleset_err = run(lambda: configure_ruleset(repo, configuration))
        report(f"ruleset '{RULESET_NAME}'", ruleset_err)

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
    push_blocked = push is not None and (
        not actions_disabled
        or (not private and (settings_err is not None or ruleset_err is not None))
    )
    if push is not None and not actions_disabled:
        # Refuse to push rather than risk a run: disabling Actions is the one
        # thing standing between a private repo and burning its own minutes.
        log("  - push main: skipped (couldn't disable Actions; refusing to risk a run)")
        failed.append("push main")
    elif push_blocked:
        reason = (
            "couldn't enable push protection"
            if settings_err is not None
            else "couldn't install public branch rulesets"
        )
        log(f"  - push main: skipped ({reason}; refusing to publish)")
        failed.append("push main")
    elif push is not None:
        fanout.append(("push main", push))

    # Submit the push first so a slow Project lookup cannot occupy its slot.
    # Reporting remains in declared order after every in-flight step settles.
    with ThreadPoolExecutor(max_workers=min(4, len(fanout))) as pool:
        futures = {}
        push_index = None
        if push is not None and not push_blocked:
            push_index = next(i for i, (_, fn) in enumerate(fanout) if fn is push)
            futures[push_index] = pool.submit(run, push)
        for i, (_, fn) in enumerate(fanout):
            if i not in futures:
                futures[i] = pool.submit(run, fn)
        errors = [futures[i].result() for i in range(len(fanout))]
    for (name, _), err in zip(fanout, errors, strict=True):
        report(name, err)

    return failed
