# Planning, effects and recovery

`--dry-run` and execution must resolve the same inputs and create the same plan.
The first beta implementation covers offline template rendering only. Its plan
contains normalized metadata, destination, stages and explicit limits. Execution
revalidates filesystem state and consumes those exact values. It does not accept
saved JSON as executable instructions or claim GitHub capabilities were checked.

## Bounded local transaction

Beta rejects nonempty paths and symlinks, acquires a per-destination writer lock,
and renders into an invocation-owned sibling directory. The complete render is
promoted with a same-filesystem rename. Exceptions and ordinary interruption clean
only the owned staging directory, an owned empty reservation and newly created
empty parents. A failed cleanup reports retained paths for inspection; it does not
replace a failure with a success message. Preexisting paths or concurrent user
files are preserved. Another beta writer is refused while the lock exists.

Execution failures record their phase and an observed outcome: output installed,
not installed, or uncertain. A rename may complete just before an interrupt or
exception reaches Python; readback of the stage/destination identities prevents a
false rollback claim or deletion of completed output. Unavailable readback leaves
the outcome uncertain and the destination retained for inspection. A failed
cleanup after successful promotion still reports that output was installed.
Directory paths are recorded before creation is attempted, including the staging
path. If an interrupt arrives after creation but before identity confirmation, the
path is retained and named instead of silently orphaned or assumed safe to delete.
A further interrupt during cleanup stops destructive operations and reports the
remaining paths and any installed output, retaining interruption exit status 130.

Abrupt process death and power failure still need a durable recovery journal; a
stale lock is not automatically safe to delete. Ordinary revalidation cannot
eliminate races with programs that ignore the lock. The installed render is a
preview: no dependency installation, tests, repository init or remote mutation
has happened. Python's normal module-loading caches belong to its installation,
not the planned project effects; use Python `-B` when checking all process writes.

## Accurate bootstrap reporting

A reported native-extension failure after other setup commands does not establish
whether a GitHub repository was created in an earlier attempt, by a concurrent
actor, or by a different installed generator version. A later absent-repository
response also cannot prove that publication never occurred. Missing Ruby headers
after uv/npm setup illustrate both prerequisite diagnosis and partial local effects.

The stable check-failure message now states only that this invocation stopped
before its creation/push steps, reports the earlier target lookup observation,
prints the exact repository URL and retained local directory, and suggests retry.
A failed remote-create request explicitly leaves creation outcome uncertain: a
server may create the repository before the client loses the response. These text
corrections do not assert that the reported incident has been fully reconstructed
or change stable bootstrap order/policy.

## Live execution journal (next increment)

Before a mutating operation, persist an intent entry with a unique invocation ID,
operation, canonical owner/name or object ID, request/plan hash and prior observed
state. Record `not_started`, `attempted`, `confirmed`, `failed` or `uncertain`.
Read back effective state before classifying a remote effect as confirmed. Store
references to credentials rather than their values; logs, manifests and URLs must
not contain embedded credentials. Record local paths and exact repository,
Project, issue, workflow-run and artifact URLs when those objects become known.

On interruption, report successful effects, unresolved effects, retained work and
the next safe action. “Nothing was published” is only defensible with appropriate
evidence for the stated scope; default to narrower factual language. A plan is
not an atomic GitHub transaction. Re-read uncertain requests before retrying and
never treat an authentication/network failure as proof an artifact is absent.

Rollback eligibility requires evidence that this invocation created the exact
object and that deletion would not destroy subsequent or shared work. Retain
useful local work and partially configured remotes for recovery; no unconditional
remote deletion. Cleanup itself needs an inspectable plan, explicit scope and the
authorization appropriate to its effects. Do not weaken protection to complete a
bootstrap or grant the owner a permanent bypass.

Actions-based bootstrap should persist this same plan/journal with bounded
retention, checksums and verified provenance. The client must diagnose identity,
repository/organization rights, Actions and workflow permissions, runner/SDK
availability and quota/cost before dispatching useful work. Queued, failed and
partially completed remote jobs must be resumable without every local toolchain.
