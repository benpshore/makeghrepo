# Python initial-publication gate

**Incomplete draft, not a verified live bootstrap fix.** The repair has three
remaining limits: strict no-bypass organization rules may block the initial push;
fresh GitHub Actions-bound check setup has not been accepted by a live test; and
uncertain Project creation can duplicate a board after a stale read.
No test result below resolves those limits or authorizes a protection bypass.

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
| Public history and PR/CI rulesets | Paginated list plus full GET bodies: active branch target, explicit `main` and default scope, exact bypass actors, complete configured rules/parameters, strict GitHub Actions `ci` and configured PR merge policy |
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
and checked on retry. Found project boards and rulesets are reused. A known incomplete
project listing cannot establish absence and therefore cannot create another board.
A lost Project-create response followed by a temporarily stale empty search is a
remaining duplicate-board risk: no durable operation ID or provider idempotency key
is recorded. Inspect an uncertain creation before rerunning; these fixtures do not
prove exactly-once Project creation.
Python reads back the replacement review gate **before** removing the legacy PR/CI
rules, preserving the old protections if a successful write did not take effect.
Customized or conflicting partial rulesets still require manual review.

If creation succeeded before `gh` added `origin`, a marker-validated Python resume
adds only the missing canonical origin and validates its effective Git URLs.
Existing origins are never replaced. A failed remote `main` lookup stops before
configuration/push; an existing remote `main` prevents all subsequent local work
from being pushed by the bootstrap. Freshly created origins are checked too, including
all effective fetch/push URLs and Git rewrites, then checked again before publication.

An unpublished committed resume must have clean checked-out `main`, including the
index and untracked files. Tracked files with `assume-unchanged` or `skip-worktree`
index flags are refused because those flags can hide modified check inputs from
Git status. Initial publication also rejects tracked Git submodules: the generated
scaffold has none, and parent status cannot establish nested check-input integrity.
It reruns checks against a frozen commit and refuses any
observed branch, ref or worktree drift. Fresh creation completes setup first,
including generated lockfiles, then commits and selects a clean `main` snapshot
before running validation. It runs each lint/test/build command once and never
stages again afterward. A failed commit callback prevents validation from starting;
a commit hook that changes the staged tree or leaves worktree changes is refused.
The same clean SHA is required after checks and before publication. Thus a later
edit cannot be silently staged into the selected publication commit.

Rust's registry preparation runs `cargo generate-lockfile` only when `Cargo.lock`
is absent. This may resolve dependency metadata over the network, but does not
compile or fetch all targets' dependencies. Existing lockfiles are not regenerated
by this step. Normal Cargo checks can report or update a stale lock; any observed
post-snapshot change then blocks publication. The fmt/clippy/test chain and shared
CMake checks remain sequential. Go's single build uses the host's null device as
its output target, retaining `./...` compilation for executable and library
packages without leaving an untracked root executable. Other
languages retain their existing setup and check scheduling. Missing Cargo or `MAKEGHREPO_SKIP_LOCAL_CHECKS` still skips its
preparation and validation; required Python/npm/Ruby setup rules are unchanged.
Offline rendering and dry-run perform none of this setup or validation.

A validation failure can now retain an unvalidated local commit. A retry checks
that commit again before any initial publication; review and commit any intended
fixes on `main` first. These are before/after drift checks, not filesystem isolation:
a change that is made and restored between observations can go undetected. Ignored
files and external check inputs are not frozen by the Git commit. Existing tool
skips remain in force; a selected SHA is not proof skipped checks ran. Settings-only
resumes do not rebuild or publish later local work.

Validation reads the working tree through Git's normal checkout and staging
semantics. Line-ending conversion and configured clean/smudge filters can make
those bytes differ from the stored commit blobs even when status is clean.
This gate does not provide byte-identical or isolated validation of custom Git
conversions; review those conversions separately.

The initial push names that frozen SHA, not the mutable local branch. Git's empty
expected-ref lease (`--force-with-lease=refs/heads/main:`) prevents updating any
existing different `main`, including an ancestor created concurrently. It is not an
unrestricted force push and does not override server rules. Git can report success
if another actor published the identical SHA, so creation ownership is not asserted.
A readback must observe the expected remote SHA. Failed/changed readback is reported
as uncertain or changed, never as proof that nothing was published; no automatic
push retry occurs. Failure setting local upstream after successful readback reports
the verified remote effect separately. The observation does not promise the branch
will remain unchanged afterward.

## Test evidence and live-validation limits

The Rust implementation is parked on its separate branch and is not changed by
this Python repair. Current-main organization handling, registry, templates and
beta isolation are retained.

`tests/test_publication_gate.py` uses stateful offline API fixtures and thread
barriers for delayed/failed writes and reads, discrepant settings, selection,
preflight and partial retries. `tests/test_bootstrap_snapshot.py` uses actual local
Git for frozen-commit publication, pre-push resume, dirty work, commit hooks,
competing branch creation, destination redirects and uncertain post-push readback.
It also wraps the real smoke orchestrator to inject edits during validation and
after it returns. Inert tool responses, not actual Cargo/npm/Ruby builds, verify
setup ordering, generated-lockfile inclusion and one execution per command.
A disposable real Python render passes `uv lock --offline` before the snapshot
and `uv lock --offline --check` afterward with identical lock bytes. This is
metadata/lock evidence, not a full generated-project build. Cargo, Swift and a
native Go toolchain are unavailable in the local repair environment. CI-only Go
build cases cover a single executable, libraries, mixed packages, multiple
executables and an invalid unimported library; they remain unverified until CI
runs. No native Cargo, Swift or Go acceptance is claimed here.
`tests/test_owner_push.py` covers partial migration and retained legacy protection.

`scripts/bootstrap-check --python-executable PATH PATH` runs 17 installed-command
scenarios against disposable local bare remotes. Current candidate distribution CI
uses the expanded Python cases. Older release checks retain their original common
cases; the new contract is not retroactively imposed on published binaries.

These checks prove control flow and local Git behavior, not GitHub acceptance.
In particular, strict public-organization PR/status rules are installed before
first branch creation with no bypass. Required status checks are configured to
apply on branch creation, so GitHub may reject the initial push. GitHub's guidance
also describes prerequisites for binding required checks to an app, which may not
yet have run on a new repository. Fresh personal and organization acceptance of
these settings needs a separately authorized live test. No restrictions are
weakened to make bootstrap pass, and passing mocks do not make this merge-ready.
A rejected push keeps the local project and remote for inspection.

The final configuration readbacks and push are separate requests. A concurrent
external settings change can occur between them; GitHub's own protections remain
the writer-side boundary. This command is not an atomic transaction or a durable
agent coordinator. The existing personal-owner bypass cannot distinguish an agent
using the owner's credentials from the owner. No live repositories are changed by
the tests.

## API references

- [Repository settings and security enablement reads](https://docs.github.com/en/rest/repos/repos)
- [Ruleset full-body reads and bypass visibility](https://docs.github.com/en/rest/repos/rules#get-a-repository-ruleset)
- [Project scopes](https://cli.github.com/manual/gh_project)
- [ProjectV2 repository links](https://docs.github.com/en/graphql/reference/projects)
- [OAuth scope headers](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps)
- [GitHub CLI API flags](https://cli.github.com/manual/gh_api)
