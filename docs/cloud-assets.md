# Beta cloud assets checkpoint

This additive integration keeps both reviewed inputs reachable:

- PR178 `427135a63a88b54f53e046e81725a2364b72b10c`: beta packaging,
  offline planning and recovery, explicit beta/script lint coverage, stable CLI
  failure reporting, its tests, root instructions, README and roadmap/recovery docs.
- Cloud checkpoint `7aa49d6386d411774574786f6dcb1cf412232549`: the cloud
  planner, read-only consumer, canonical producer, beta seed assets and fixtures.
  The seven PR178-only paths are retained byte for byte; the ten shared file
  contents match in both inputs. Existing executable script modes are retained.
- PR175 `891b9f62b309995357f67003159e7c8f53249509`: the checkpoint's seed
  assets match this input; seed validation differs only in its beta namespace.
  Stable lock/bootstrap and publication changes remain separate work.
- PR177 `cc0af490ddb67927fa569a5b590ba3cd5aacbd01`: publication guards
  remain a prerequisite for future live operations, not for plan/fetch. They are
  not enabled by this integration.

PR178's original head is preserved at
`checkpoint/pr178-before-cloud-integration-20261015`. Integration uses existing
PR178; no new PR, protected-branch merge or PR closure is part of this work.

## Initial slice

Prebuild one canonical Python starter in makeghrepo CI. Reuse the shared renderer
and the beta seed, pin the builder independently of the client host, provision
and build in Actions, and accept only a completed, integrity-checked result.
Repository owner/name are a separate configuration file. Distribution/import
names, console entry points, dependency graphs, platform tags and built bytes
are immutable. A different package identity or target requires another cloud
build; it is never repaired with local search-and-replace or dependency execution.

The client only validates data and reads GitHub run/artifact metadata via gh.
No repository creation, settings mutation, publication, release, secret handling
or live clone is enabled by this increment. An unverified artifact never becomes
a successful local result. Fixture tests do not establish live GitHub acceptance.

## Validation and recovery evidence

The selected cloud VM is the only development workspace used for this integration.
Its shell GitHub, python.org and PyPI connections return proxy HTTP 403, including
an escalated Git fetch. The connected GitHub API supplies refs and Git objects;
imported blob, tree and commit IDs are verified before use. The local imports have
explicit shallow boundaries at the two input heads; their full histories remain
on GitHub. A normal additive commit retains both parents remotely.

The VM has Python 3.12/3.13, while the project requires 3.14. Downloading 3.14 and
locked dependencies is blocked. Local archive fixtures, CLI dry-run dispatch,
shared-source manifest and patch checks are useful evidence, but cannot replace
supported-runtime full tests, lint, packaging or native checks in existing CI.
Attempting the full beta suite on 3.12 encounters the unchanged 3.14 exception
syntax in `execute.py`; no project/runtime requirements are relaxed to hide that.

Historical results apply only to their exact source heads:

- PR178's [CI 38527335717](https://github.com/benpshore/makeghrepo/actions/runs/38527335717)
  passed at `427135a63a88b54f53e046e81725a2364b72b10c`, including 493
  stable and 42 beta tests and installed stable/beta coexistence.
- The salvaged [CI 38764933080](https://github.com/benpshore/makeghrepo/actions/runs/38764933080)
  passed all 46 jobs at `7aa49d6386d411774574786f6dcb1cf412232549`,
  including 489 stable and 62 beta tests, the actual producer and coexistence.
  Those tests did not catch the ZIP or CLI regressions described below.

The integration adds regression fixtures and must pass the full existing CI on
its own head. The PR retains explicit Ruff lint/format checks for `beta`,
`scripts/beta-check` and `scripts/regen-beta-sources`. Its final workflow removes
the temporary checkpoint-specific push trigger. Release triggers remain unchanged;
golden production is restricted to main pushes and manual runs. Checkpoint/PR
artifacts remain test evidence, not consumable trusted assets.

## Implemented contract

`makeghrepo-beta cloud plan` takes an explicit full makeghrepo source SHA and
daughter owner/name. It emits schema 1 with a fixed source repository/workflow,
target profile, seed digest, asset key and read-only permission requirements.
The asset key excludes daughter identity; the plan ID includes it. Unknown
fields, changed profiles, noncanonical identities, duplicate JSON keys and
mutable source refs are rejected. No client host probe selects a daughter tool.

`cloud fetch --dry-run` uses the same run/destination validator as execution,
does not invoke gh, and explicitly reports remote state as unverified. Actual
fetch uses only GETs through gh, each bounded to 30 seconds and a response size.
It validates completed/successful run metadata, exact repository/source/workflow,
trusted event, run attempt, complete unique artifact listing, expiry, size and
GitHub's SHA-256 archive digest. The run and artifact are read again before local
installation. PR and fork artifacts, missing readback, failure and rerun races
are rejected. A caller must review the pinned source; hashes and GitHub metadata
do not establish the safety of a compromised maintainer or builder.

The result includes the source/profile/seed binding, run ID/attempt, all required
check outcomes and a complete file inventory (hash, size, mode). ZIP parsing first caps the actual central directory at 512 entries and 1 MiB,
before constructing `ZipFile`. It caps compressed archives and aggregate expanded
bytes at 64 MiB, with 16 MiB per expanded member. It rejects traversal, links,
special files and duplicate/case-colliding paths. Raw DEFLATE output is read with
an explicit per-call bound, complete-stream EOF/size/CRC checks, and no unbounded
flush. Ordinary and fixed-record ZIP64 directories and streaming data descriptors
are supported; split disks, prepended executables and trailing streams are refused.

A forged member can declare one byte and that prefix's CRC while containing a
much larger compressed payload. Python's `ZipExtFile` slices output to the declared
size even during chunked reads, so comparing its returned length to that size does
not validate actual expansion. Regression fixtures reproduce that behavior and
verify rejection, along with forged directory counts, directory allocation bounds,
compressed/member/aggregate limits, corrupt/truncated streams and ZIP64 inputs. The client checks the full Python manifest/lock graph, wheel package,
version, tag, entry point, source and every RECORD hash without importing code.

The existing CI now has a read-only `beta-assets` job on Ubuntu 24.04, using
CPython 3.14.8 and uv 0.12.23. It renders the shared canonical Python console
starter, verifies the packaged seed, checks the lock offline, provisions locked
dev dependencies, formats/lints/tests and compiles the source, builds a wheel
offline, and installs/tests it in a clean environment. Only after success does
it upload a seven-day candidate. The aggregate `ci` job also requires this job.
The same workflow tests beta packaging and stable/beta tool coexistence.

Build backends are pinned to hatchling 1.32.0 and uv-dynamic-versioning 0.14.0.
Their transitive closure is resolved and hash-locked during makeghrepo prebuilding,
then installed from that lock before the offline build. The complete build lock
is included in the result. It is not yet a separately reviewed, checked-in build
lock, and the Ubuntu runner image is not immutable. Bit-reproducible builds are
not claimed. None of this resolution, installation or building occurs in the
consumer. The producer uses a fresh credential-free environment for daughter
tools; there are no write-scoped GitHub tokens or publication jobs in this slice.

## Identity and recovery limits

Only `identity.json` varies between daughters sharing an asset. No compiled file,
wheel metadata, import, entry point, lock package, license or workflow is rewritten.
Canonical source docs/workflows still refer to the canonical starter. Completing
daughter-specific metadata requires a later cloud materialization/validation
step; accepting this bundle is not a claim those canonical URLs are daughter URLs.
Changing package identity, dependencies, layout, license or native target needs
a separately supported cloud build/profile. Only this Python console profile is
implemented; its package requires Python 3.14 or later and is tested on the pinned
Linux runtime. Broader Python compatibility/native stacks remain separate work.

Installation uses PR178's writer lock, owned staging and atomic rename. All
payload bytes are checked before promotion, with a receipt containing the plan,
run, artifact digest and file inventory. A completed retry must match every byte
and mode; existing edits or a different plan/run are retained and rejected.
Abrupt process death can leave a stage/lock; these are retained for inspection,
never automatically deleted or assumed safe to steal. The receipt is not a
power-loss-safe transaction log, and unrelated programs need not honor the lock.
There is no blind remote redispatch, recreation, rollback or deletion. Live
repository creation, effective-setting readback, publication and verified clone
remain disabled until their full cloud transaction is implemented and tested.

## Verification and integration order

The combined regression suite includes the existing planner/recovery, producer
stage failures, wheel identity/RECORD, readback races and repeat-consumption cases.
New cases cover the archive limits above and the valid project name `cloud` in
positional, options-first and intermixed offline forms, in both dry-run and
execution dispatch. Only recognized `cloud plan` and `cloud fetch` prefixes select
the cloud command path.

Run full tests with the supported runtime and locked dependencies:

```sh
uv sync --locked
uv run pytest
PYTHONPATH=beta/src:src uv run pytest beta/tests
uv run ruff format --check beta scripts/beta-check scripts/regen-beta-sources
uv run ruff check beta scripts/beta-check scripts/regen-beta-sources
uv run python scripts/regen-beta-sources --check
```

Stable source retains PR178's CLI reporting correction and regression tests; it
is not identical to main. Stable rendering/templates, Rust, golden files, root
packaging/lock and release workflows are unchanged from the inspected main.
The source manifest, installed distributions and producer remain required CI
gates; the Rust renderer is parked on the `rust` branch (#150). Passing historical or local fixture runs alone does not
establish that this combined tree passes them.

Existing upstream CI was independently re-read through GitHub: PR175's
[CI 37303024998](https://github.com/benpshore/makeghrepo/actions/runs/37303024998)
is failed; PR177's
[CI 38169735386](https://github.com/benpshore/makeghrepo/actions/runs/38169735386)
and PR178's
[CI 38527335717](https://github.com/benpshore/makeghrepo/actions/runs/38527335717)
are successful at the exact heads listed above. Those results are upstream
evidence, not tests of this checkpoint.

Keep existing **PR178** as the integration PR. Other PRs retain their original
heads and remain open; this task does not authorize closing them or claim their
unique changes landed. The historical inventory is in `reconciliation-heads.json`.

1. Preserve PR178 and the cloud checkpoint as parents of the additive integration.
   Keep stable rendering unchanged. Run the full existing CI, including the
   beta-assets and installed coexistence gates, on the exact integration head.
2. Resolve producer/format/integration failures there. A reviewed manual run at
   that exact source is the first consumable artifact; PR candidates remain test
   evidence only. Exercise authenticated artifact retrieval/readback before any
   live daughter work. This checkpoint has no trusted prebuild run yet.
3. Reconcile PR177's required setup/readback/publication ordering with PR175's
   immutable publication-commit validation behind beta's future live path. Add
   persisted remote intent/uncertainty reconciliation, a cloud metadata
   materializer and a verified-source clone gate before enabling live bootstrap.
4. If adopting PR175's stable shared lock template, generate trusted goldens by
   the existing main/manual artifact procedure, validate them completely.
   Do not carry its known failing goldens forward or
   silently restrict stable language support.
5. Keep PR145's release refactor and its missing required ci context outside
   this implementation; preserve it in the consolidation inventory for separate
   review. No protected merge, release or live provisioning is authorized here.
