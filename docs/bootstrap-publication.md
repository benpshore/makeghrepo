# Python initial-publication gate

The initial `main` push is a separate final operation. It is never submitted to
an executor alongside setup. All selected writes must settle, and each successful
step must pass an independent effective-state readback before publication.
Settings-only resumes still repair configuration but never publish later local
commits. Failures retain both the local project and remote; no automatic deletion
or credential changes are performed.

## Required operations

The CLI currently selects all these operations, including the Project board.
The Python API's `project=False` excludes both its prerequisite and setup/link
checks; a prepared plan must match that selection.

| Operation | Independent verification |
| --- | --- |
| Repository settings, both visibilities | GET repo: identity, visibility, admin access; wiki, merge modes, branch updates/deletion, squash title/message and public auto-merge |
| Public secret scanning and push protection | GET repo `security_and_analysis`: both statuses enabled |
| Public history and PR/CI rulesets | Paginated list plus full GET bodies: active branch target, explicit `main` and default scope, exact bypass actors, complete rules/parameters including preserved existing restrictions, strict GitHub Actions `ci` and squash PR gate |
| Private Actions disablement | GET Actions permissions: `enabled: false` |
| Dependabot alerts and security fixes, both visibilities | GET alerts must succeed (enabled is HTTP 204; disabled is 404); GET fixes: `enabled: true`, `paused: false` |
| Public private-vulnerability reporting | GET reporting: `enabled: true` |
| Both labels | GET each exact label: name and configured color |
| Notification subscription | GET subscription: `subscribed: false`, `ignored: true` |
| Selected Project board and link | GraphQL read of that project's title and paginated repository links: exact repo present |

Private auto-merge remains **advisory**, matching the existing documented Free-plan
policy: GitHub may silently leave it off. Its absence is logged explicitly and
does not defeat the private publication gate. Public auto-merge is required.
Private rulesets, scanning and vulnerability reporting retain their documented
unsupported-feature skips. Successful API writes alone satisfy none of the
required readbacks. Missing fields, malformed responses, authorization failures,
stale state and discrepancies block the push; rerun after resolving them.

## Before creation

Python validates selection types, owner identity/ID, label colors and serialized
payloads. Read-only checks inspect the classic token's advertised OAuth scopes
(`repo` or public `public_repo`, `workflow`, selected `project`) and access to the
selected Projects API. It neither extracts nor refreshes credentials.

Fine-grained/App tokens may not advertise classic scopes. Those headers are not
invented or treated as a proof of write permission. Repository-specific admin
access, owner and visibility are checked after creation and before configuration,
and again during settings readback. Endpoint-specific write permissions and
server-side feature support can still fail after creation. The retained empty or
partially configured remote is the recovery resource, not an atomic transaction.

## Partial state and retries

Independent writes can finish even when another step fails. Their state is retained
and safely reapplied. Existing project boards and rulesets are reused. An incomplete
project listing cannot establish absence and therefore cannot create another board.
Python reads back the replacement review gate **before** removing the legacy PR/CI
rules, preserving the old protections if a successful write did not take effect.
Customized or conflicting partial rulesets still require manual review.

If creation succeeded before `gh` added `origin`, a marker-validated Python resume
adds only the missing canonical origin and validates its effective Git URLs.
Existing origins are never replaced. A failed remote `main` lookup stops before
configuration/push; an existing remote `main` prevents all subsequent local work
from being pushed by the bootstrap. A rejected initial push is safely retryable.

## Rust differences and test evidence

Rust is unchanged. It lacks Python's precreation identity/scope/Project checks,
full setup completion gate, effective-setting readbacks, explicit `main` ruleset
scope, dry-run and missing-origin recovery. Its initial push can overlap setup.
The common distribution fixtures compare resulting settings/policy, permitting
only the documented scope difference; they do not assert publication-order parity.

`tests/test_publication_gate.py` uses stateful offline API fixtures and thread
barriers to exercise delayed/failed writes and reads, discrepant settings,
protections changed by later setup, selection/preflight failures and retries.
`tests/test_owner_push.py` covers partial migration and retained legacy protection.
`scripts/bootstrap-check --python-executable PATH PATH` runs 17 real-Git cases
against disposable local bare remotes, including preflight failure, creation before
origin, required-operation failure, readback discrepancies, project creation before
link failure, safe retries and later-local-commit preservation. Distribution CI
runs the expanded cases for the Python wheel and five common cases for Rust.

These fixtures prove control flow and recovery behavior, not live GitHub behavior.
The final readbacks and push are separate requests: a concurrent external settings
change can occur between them. Existing protections are not removed to work around
push failures. No live repositories are needed by these checks.

## API references

- [Repository settings and security enablement reads](https://docs.github.com/en/rest/repos/repos)
- [Ruleset full-body reads and bypass visibility](https://docs.github.com/en/rest/repos/rules#get-a-repository-ruleset)
- [Project scopes](https://cli.github.com/manual/gh_project)
- [ProjectV2 repository links](https://docs.github.com/en/graphql/reference/projects)
- [OAuth scope headers](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps)
- [GitHub CLI API flags](https://cli.github.com/manual/gh_api)
