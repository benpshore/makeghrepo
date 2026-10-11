"""GitHub setup via the ``gh`` CLI (which owns auth; we never touch tokens).

Every step is idempotent, so ``makeghrepo NAME`` can be re-run
after a partial failure.
"""

from __future__ import annotations

import json
import re
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
    project: bool = True


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


def prepare_configuration(
    owner_login: str, private: bool, *, project: bool = True
) -> ConfigurationPlan:
    """Prepare and validate payloads before creating the GitHub repository.

    Public rulesets need the personal account ID as a bypass actor. Fetch and
    validate it now so identity or serialization errors cannot strand a newly
    created, unprotected repository. The later repo-scoped call verifies that
    the created repo has this same owner before installing those rules.
    """
    if type(private) is not bool or type(project) is not bool:
        raise GhError("visibility and project selection must be booleans")
    try:
        user = api("GET", "user")
    except (GhError, ValueError) as exc:
        raise GhError(f"could not verify the repository owner before creation: {exc}") from exc
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
    rulesets: tuple[dict[str, Any], ...] = ()
    if not private:
        rulesets = _ruleset_bodies(owner_id)

    # Inspect advertised classic OAuth scopes without reading or changing tokens.
    # Fine-grained/App tokens do not advertise this header: their repo-specific
    # permissions can only be checked once the repository exists.
    headers = gh("api", "--include", "--silent", "user")
    scopes_header = next(
        (
            line.split(":", 1)[1]
            for line in headers.splitlines()
            if line.lower().startswith("x-oauth-scopes:")
        ),
        None,
    )
    if scopes_header is not None:
        scopes = {scope.strip() for scope in scopes_header.split(",")}
        required = {"workflow"} | ({"project"} if project else set())
        missing = required - scopes
        if "repo" not in scopes and (private or "public_repo" not in scopes):
            missing.add("repo" if private else "repo or public_repo")
        if missing:
            raise GhError("missing GitHub token scopes: " + ", ".join(sorted(missing)))
    if project:
        try:
            listing = json.loads(
                gh("project", "list", "--owner", owner_login, "--limit", "1", "--format", "json")
            )
        except (GhError, ValueError) as exc:
            raise GhError(f"could not preflight the selected project setup: {exc}") from exc
        if not isinstance(listing, dict) or not isinstance(listing.get("projects"), list):
            raise GhError("could not verify access to the selected project setup")
    if any(not re.fullmatch(r"[0-9a-fA-F]{6}", color) for color in LABELS.values()):
        raise GhError("invalid label color in GitHub configuration")
    settings = settings_body(private)
    try:
        # Validate the exact payload shapes that will later be sent to GitHub.
        json.dumps({"settings": settings, "rulesets": rulesets, "labels": LABELS}, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise GhError("could not prepare GitHub settings before repository creation") from exc
    return ConfigurationPlan(owner_login, owner_id, private, settings, rulesets, project)


def configure_ruleset(
    repo: str, configuration: ConfigurationPlan | None = None
) -> tuple[dict[str, Any], ...]:
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
        # A successful write need not have taken effect. Verify the replacement
        # gate before removing legacy PR/CI requirements from the history gate.
        verify_rulesets(repo, (body,))
    return tuple(bodies)


def _contains(actual: Any, expected: Any) -> bool:
    """Require every requested field; tolerate server-added object metadata."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _contains(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        remaining = list(actual)
        for value in expected:
            match = next((i for i, item in enumerate(remaining) if _contains(item, value)), None)
            if match is None:
                return False
            remaining.pop(match)
        return True
    return type(actual) is type(expected) and actual == expected


def _require(actual: Any, expected: Any, setting: str) -> None:
    if not _contains(actual, expected):
        raise GhError(f"effective {setting} does not match the required configuration")


def verify_rulesets(repo: str, bodies: tuple[dict[str, Any], ...]) -> None:
    existing = []
    page = 1
    while True:
        batch = api("GET", f"repos/{repo}/rulesets?includes_parents=false&per_page=100&page={page}")
        existing.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    for body in bodies:
        matches = [rule for rule in existing if rule.get("name") == body["name"]]
        if len(matches) != 1:
            raise GhError(f"could not read back a unique ruleset {body['name']!r}")
        saved = api("GET", f"repos/{repo}/rulesets/{matches[0]['id']}")
        _require(saved, body, f"ruleset {body['name']!r}")
        if body["name"] == REVIEW_RULESET_NAME:
            rules = {rule["type"]: rule for rule in saved["rules"]}
            checks = rules["required_status_checks"]["parameters"]
            _require(
                checks,
                {
                    "strict_required_status_checks_policy": True,
                    "do_not_enforce_on_create": False,
                },
                "required CI gate",
            )
            if not any(
                _contains(
                    check,
                    {
                        "context": REQUIRED_CHECK,
                        "integration_id": GITHUB_ACTIONS_APP_ID,
                    },
                )
                for check in checks["required_status_checks"]
            ):
                raise GhError("effective ruleset is missing the required GitHub Actions ci check")
            _require(
                rules["pull_request"]["parameters"],
                {
                    "dismiss_stale_reviews_on_push": True,
                    "required_review_thread_resolution": True,
                    "allowed_merge_methods": ["squash"],
                },
                "required PR gate",
            )


def verify_repository(repo: str, configuration: ConfigurationPlan, *, settings: bool) -> None:
    saved = api("GET", f"repos/{repo}")
    saved = deepcopy(saved)
    if isinstance(saved, dict) and isinstance(saved.get("owner"), dict):
        login = saved["owner"].get("login")
        if isinstance(login, str) and login.casefold() == configuration.owner.casefold():
            saved["owner"]["login"] = configuration.owner
    _require(
        saved,
        {
            "private": configuration.private,
            "owner": {"login": configuration.owner, "id": configuration.owner_id, "type": "User"},
            "permissions": {"admin": True},
        },
        "repository identity, visibility and admin access",
    )
    if settings:
        required = deepcopy(configuration.settings)
        if configuration.private:
            # Existing policy: Free private repos may silently reject auto-merge.
            # It is advisory there, not a publication requirement.
            required.pop("allow_auto_merge", None)
        _require(saved, required, "repository settings")


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
    listing = json.loads(full)
    projects = listing["projects"]
    number = next((p["number"] for p in projects if p["title"] == title), 0)
    if not number and (len(projects) >= 1000 or listing.get("totalCount", 0) > len(projects)):
        raise GhError("project listing is incomplete; refusing to create a duplicate board")
    return number


def configure_project(repo: str) -> int:
    """Link the repo to a same-named project, creating one if it doesn't exist."""
    owner, title = repo.split("/")
    number = _find_project_by_title(owner, title)
    if not number:
        created = gh("project", "create", "--owner", owner, "--title", title, "--format", "json")
        number = json.loads(created)["number"]
    gh("project", "link", str(number), "--owner", owner, "--repo", repo)
    return number


PROJECT_LINK_QUERY = """
query($owner: String!, $number: Int!, $cursor: String) {
  user(login: $owner) {
    projectV2(number: $number) {
      title
      repositories(first: 100, after: $cursor) {
        nodes { nameWithOwner }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""


def verify_project(repo: str, number: int) -> None:
    owner, title = repo.split("/")
    cursor = None
    seen = set()
    while True:
        args = [
            "api",
            "graphql",
            "-f",
            f"query={PROJECT_LINK_QUERY}",
            "-f",
            f"owner={owner}",
            "-F",
            f"number={number}",
        ]
        if cursor is not None:
            args += ["-f", f"cursor={cursor}"]
        board = json.loads(gh(*args))["data"]["user"]["projectV2"]
        _require(board, {"title": title}, "project title")
        repos = board["repositories"]
        if any(node["nameWithOwner"].casefold() == repo.casefold() for node in repos["nodes"]):
            return
        info = repos["pageInfo"]
        if info["hasNextPage"] is not True:
            raise GhError("effective project link is missing")
        cursor = info["endCursor"]
        if not cursor or cursor in seen:
            raise GhError("could not finish reading project repository links")
        seen.add(cursor)


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
    """Apply every selected step, read back effective state, then publish main.

    All configured operations are required (Project only when selected). Writes
    may run concurrently with other writes, never with the push. A failed write
    or readback leaves the remote intact, blocks publication, and can be retried.
    A settings-only retry never pushes later local work. Private auto-merge is
    the sole advisory setting, as documented for the existing Free-plan policy.
    """
    if configuration is None:
        configuration = prepare_configuration(repo.split("/", 1)[0], private, project=project)
    if (
        configuration.private != private
        or configuration.project != project
        or configuration.owner.casefold() != repo.split("/", 1)[0].casefold()
    ):
        raise GhError("prepared GitHub settings do not match owner, visibility or selected options")

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

    def blocked() -> list[str]:
        if push is not None:
            log("  - push main: skipped (required setup failed; refusing to publish)")
            failed.append("push main")
        return failed

    # After creation (or on resume), reject a wrong/changed remote before writes.
    identity_err = run(lambda: verify_repository(repo, configuration, settings=False))
    if identity_err:
        report("repository access", identity_err)
        return blocked()

    if private:
        log("  - private repo: skipping ruleset, secret scanning, vuln reporting (need paid plan)")
        log("  - private auto-merge is advisory (may be unavailable on GitHub Free)")

    # Each successful write has an independent GET verifier. Do not trust the
    # response body from PATCH/PUT/POST to establish effective configuration.
    verifiers: list[tuple[str, Callable[[], object]]] = []

    def apply(name: str, write: Callable[[], object], verify: Callable[[], object]) -> None:
        err = run(write)
        report(name, err)
        if err is None:
            verifiers.append((name, verify))

    apply(
        "repo settings",
        lambda: api("PATCH", f"repos/{repo}", configuration.settings),
        lambda: verify_repository(repo, configuration, settings=True),
    )
    if private:
        apply(
            "disable actions",
            lambda: disable_actions(repo),
            lambda: _require(
                api("GET", f"repos/{repo}/actions/permissions"),
                {"enabled": False},
                "Actions disablement",
            ),
        )
    else:
        desired_rulesets = configuration.rulesets

        def install_rulesets() -> None:
            nonlocal desired_rulesets
            desired_rulesets = configure_ruleset(repo, configuration)

        # Settings-only retries still repair protection after a failed PATCH.
        if push is None or "repo settings" not in failed:
            apply(
                f"ruleset '{RULESET_NAME}'",
                install_rulesets,
                lambda: verify_rulesets(repo, desired_rulesets),
            )

    alerts = f"repos/{repo}/vulnerability-alerts"
    fixes = f"repos/{repo}/automated-security-fixes"
    subscription = f"repos/{repo}/subscription"

    def verify_dependabot() -> None:
        # Alerts return 204 if enabled, and 404 (gh failure) if disabled.
        api("GET", alerts)
        _require(api("GET", fixes), {"enabled": True, "paused": False}, "Dependabot fixes")

    def verify_labels() -> None:
        for name, color in LABELS.items():
            saved = api("GET", f"repos/{repo}/labels/{name}")
            _require(saved, {"name": name}, f"label {name!r}")
            if saved.get("color", "").casefold() != color.casefold():
                raise GhError(f"effective label {name!r} color does not match")

    fanout: list[tuple[str, Callable[[], object], Callable[[], object]]] = [
        (
            "dependabot alerts + security fixes",
            lambda: (
                api("PUT", alerts),
                api("PUT", fixes),
            ),
            verify_dependabot,
        ),
        (
            "labels: " + ", ".join(LABELS),
            lambda: [
                gh("label", "create", name, "--repo", repo, "--color", color, "--force")
                for name, color in LABELS.items()
            ],
            verify_labels,
        ),
        (
            "mute notifications (watch: ignore)",
            lambda: api("PUT", subscription, {"subscribed": False, "ignored": True}),
            lambda: _require(
                api("GET", subscription),
                {"subscribed": False, "ignored": True},
                "notification subscription",
            ),
        ),
    ]
    if not private:
        pvr = f"repos/{repo}/private-vulnerability-reporting"
        fanout.insert(
            1,
            (
                "private vulnerability reporting",
                lambda: api("PUT", pvr),
                lambda: _require(api("GET", pvr), {"enabled": True}, "vulnerability reporting"),
            ),
        )
    if project:
        project_number = 0

        def link_project() -> None:
            nonlocal project_number
            project_number = configure_project(repo)

        fanout.append(("project board", link_project, lambda: verify_project(repo, project_number)))

    with ThreadPoolExecutor(max_workers=min(4, len(fanout))) as pool:
        futures = [pool.submit(run, write) for _, write, _ in fanout]
        errors = [future.result() for future in futures]
    for (name, _, verify), err in zip(fanout, errors, strict=True):
        report(name, err)
        if err is None:
            verifiers.append((name, verify))

    # Every write has settled before this final gate. Missing, stale, malformed,
    # unauthorized, or discrepant readback is a failure, never permission to push.
    for name, verify in verifiers:
        err = run(verify)
        if err is not None:
            report(name + " readback", err)
    if failed:
        return blocked()
    if push is not None:
        report("push main", run(push))
    return failed
