# One open PR: PR178 implementation and rollout

This machinery is part of the existing PR178 integration task. The parent owns
scope, review and completion; workers do not create competing PRs or declare the
task complete independently. The maximum is **one open PR per repository**,
including drafts and bots. Zero is permitted; a repository with zero PRs may
later have one. Current makeghrepo work must continue on PR178.

## Current state: disabled, no closures authorized

`policy.json` is in `audit` mode and the trusted workflow sets
`ONE_OPEN_PR_APPLY=0`. Both must be changed through parent-reviewed default-branch
code before any close request is possible. This PR does not deploy the guard to
default, change settings/credentials/protections, or close existing PRs. No
separate guard implementation or PR is needed.

The repository already has `task` issues and a `Closes #` line in its PR template.
PR178 currently has no bound task issue: `task_issue` remains null. An inspection
of all 178 existing issue/PR records on 2026-10-15 identified existing open
[issue #154, the explicit no-side-effects dry-run contract](https://github.com/benpshore/makeghrepo/issues/154),
as the proposed binding for PR178's original offline-planning task. It currently
has only the `agent-created` label, so it is not yet eligible for enforcement.
The parent must review the beta-only acceptance boundary and add the existing
`task` label before approving this binding. Related architecture/isolation work
in #149/#163 and the parent epic #169 is not claimed complete; the synthetic
test ticket #123 is unrelated TypeScript work and is not the proposed binding.
No issue, label, closing reference or policy binding was changed by this proposal.

Audit reports the missing binding. Enforcement requires the parent to approve
the actual task, set its number in policy and put one standalone `Closes #N` line in
PR178's description. The issue must be open, in this repository, labelled `task`,
and must not itself be a PR. Comments, code examples, multiple tickets, other
repositories and different closing keywords do not satisfy this strict binding.
Four-space/tab-indented code and fenced examples (including unclosed fences)
cannot supply a binding; a real binding line allows at most three leading spaces.
This checks metadata deterministically; the parent still reviews actual scope,
acceptance criteria and evidence. It does not create another tracking system or
claim to authenticate the parent through a GitHub username guess.

## Retention and migration

The fixed migration cutoff is PR/issue number **178**, recorded both in trusted
code and policy. Every PR numbered 178 or below remains protected from automated
closure, including reopened backlog. The cutoff never advances on a sweep.
The existing backlog is a migration exception, not evidence that the maximum has
already been achieved. The guard can prevent additional accumulation after
activation while the parent preserves and reconciles that backlog separately.

PR178 is always retained while open, regardless of draft status or creation order.
If it is no longer open, the deterministic fallback retains the oldest open PR
by creation time, then number. That PR still needs the approved task binding
before any other PR can be closed. The parent must review the next task/pin when
the current task ends. The script never creates PRs, deletes branches, merges,
rewrites commits, posts comments, or alters repository settings.

Before any manual backlog closure, preserve each exact head under a checkpoint
ref, inventory unique paths/commits and review/CI links, and verify that the
intended integration contains that work. `docs/reconciliation-heads.json` is a
historical input inventory, not a blanket closure approval. This implementation
never automatically closes the protected backlog, even after it is indexed.

## Trusted execution and recovery

The privileged workflow handles `pull_request_target` only for main, and accepts
scheduled/manual runs only on the repository's default-branch ref. It checks out
the event's exact trusted default-branch SHA, never PR head or merge code. It does
not interpolate PR content into shell commands or install dependencies. The
separate test workflow runs candidate code with `contents: read` and no write
token, and is required by the existing `ci` aggregate.

Immediately before **every** close request, including retries, the event-time
code resolves the live default branch and requires its head to match the executing
`GITHUB_SHA`. It reads policy as data at that exact commit, verifies that the live
policy still matches and permits enforcement, then checks the live default branch
and head again. A later commit, including a pause in either policy or the workflow
switch, invalidates queued/rerun executions from an older head. Missing provenance,
changed defaults/policy, failed reads and exhausted retries stop closure requests;
the script never falls back to its event-time policy or executes fetched code.
To pause after future activation, commit `audit` or `ONE_OPEN_PR_APPLY=0` through
reviewed default-branch changes. Dispatch the current head for recovery after
review; rerunning an old execution cannot regain authority while that head differs.

One repository-wide Actions concurrency group uses `cancel-in-progress: false`
and `queue: max`; opening or editing another PR cannot cancel active reconciliation.
GitHub's queue is finite. Each run scans the entire open inventory, with complete
pagination, and requires two matching snapshots before writes. Failed pages,
duplicates, unstable inventories and exhausted retries cause failure, never a
partial-list decision. Author and draft filters are deliberately absent.

Each close records the PR number, planned head and authorizing default-branch SHA
in Actions logs, rechecks the candidate and retained PR, and validates the task again.
New candidate commits or a closed/missing retained PR stop the run. Transient
GET failures use bounded retries; close failures are read back before a retry to
handle a server-side success followed by a lost response. Permanent permission
errors fail. No 404/authentication failure is treated as proof of absence.
Every successful readback records its actual PR state and head SHA before checking
them against the planned head. A commit racing with PATCH produces a `head-mismatch`
record and stops retries and later closures, even if that PR was closed or the
response was lost. Unavailable readback leaves an attempted, uncertain effect.
Confirmed closures and uncertain/attempted effects remain in the log for recovery.
Retry a failed run through default-branch workflow dispatch after inspection;
already-closed PRs are harmless, and branches/commits remain available.

The [PR update API](https://docs.github.com/en/rest/pulls/pulls#update-a-pull-request)
does not expose a head-SHA precondition for closure or an atomic transaction over
PR states and the default-branch policy. The retained PR, candidate head or policy
can change after the last read and before PATCH. Live checks bound this window;
they cannot recall an in-flight request. Post-effect PR readback detects observed
head/retention races, and the next live authorization rejects a later pause.
These checks stop later effects and report recovery targets. The parent must
inspect/reopen the appropriate preserved PR if necessary; the script never
blindly reopens a PR that a person intentionally closed.

## Limits and acceptance

This is **asynchronous after-creation enforcement**. GitHub can briefly have more
than one open PR; this workflow cannot synchronously prevent creation. Events
caused by `GITHUB_TOKEN` can be suppressed, so hourly default-branch sweeps and
manual dispatch reconcile the full inventory without relying on an event's PR
number. Those runs also recover missed/overflowed events and failed runs. Actions
being disabled, unavailable or denied a write token remains a visible blocker.
No claim of a platform-wide synchronous limit or deployment to other repos is made.

Run `node --test .github/one-open-pr/reconcile.test.mjs`. Tests use synthetic APIs,
not real PR closures. They cover complete pagination, bot drafts and suppressed
events, stable selection, serialized repeated deliveries, cutoff protection,
transient/permanent failures, lost responses, changed heads, retained-PR races,
stale queued/rerun code after a pause, policy/ref changes between checks/closures,
post-close head races, indented/fenced task examples, recovery records, and workflow
trust/concurrency constraints.
Unit tests verify the scheduling contract and simulated serial runs; actual
GitHub queue delivery is a platform guarantee, not a local concurrency proof.

Activation requires parent review of the exact tested tree, the concrete task
binding, backlog preservation plan and the two disabled switches. Merging to
default would also trigger this repository's release pipeline, so this task does
not perform that merge or imply release authorization.
