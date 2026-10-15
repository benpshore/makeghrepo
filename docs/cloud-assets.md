# Beta cloud assets checkpoint

This branch selectively integrates code from the following inspected heads, all
based on main `bf1ed67859d8786c16f41196765d3ef1582e685f`:

- PR178 `427135a63a88b54f53e046e81725a2364b72b10c`: separate beta packaging,
  offline planner, staged recovery, and their tests. Stable CLI message changes,
  root instructions, roadmap and unrelated CI edits are not imported.
- PR175 `891b9f62b309995357f67003159e7c8f53249509`: packaged Python seed and
  complete manifest/lock validation, confined to beta resources. Stable templates,
  bootstrap restrictions, immutable-publication changes and golden files are not
  imported. Its required golden/parity failures remain unresolved in that PR.
- PR177 `cc0af490ddb67927fa569a5b590ba3cd5aacbd01`: the completion/readback
  contract informs the cloud-result gate. Its remote writes, policy changes and
  stable bootstrap implementation are not imported. They remain a prerequisite
  for a future live publication path, with immutable source validation from PR175.

No PR was merged. PR145 release infrastructure is outside this branch. The
uncommitted Mac implementation was unavailable and was not recovered.

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

## Environment evidence

The saved cloud workspace has no second worktree or other visible worker. GitHub
reported zero in-progress Actions runs when inspected on 2026-10-15. Shell GitHub
and PyPI requests fail through the environment proxy (HTTP 403/tunnel errors),
including an explicitly network-enabled attempt. The connected read-only GitHub
API supplied branch/PR metadata and actual patches without changing environments.
Installed Python is 3.12/3.13; the repository requires 3.14. Locked sync cannot
download that interpreter. Stdlib fixtures can run here; supported-version full
tests, builds and native checks require the existing authorized Actions CI.

This is a development checkpoint, not a claim that cloud prebuilding or the
complete redesign has passed its integration gates.

## Remote checkpoint and CI access

The renewed instruction authorizes pushing this checkpoint, without opening a
new PR. The VM proxy still rejects GitHub/PyPI CONNECT requests before TLS, even
with a network-enabled escalation; changing dependency versions does not repair
that policy. The connected GitHub API can write repository Git data. The four
local checkpoints were replayed through that API with each tree SHA verified
identical, preserving their original IDs in the commit messages and local bundle:

| Local checkpoint | Remote replay |
| --- | --- |
| `f0100d1ed96ec9a327e97bbcd4d0d44fc49c27fd` | `bc253e48d94c8b4ff8972bba32501ce55026fdbb` |
| `9d97615acc4304f244b78dcaa67a954962e428be` | `4fcf7aaa3cde9230d93c0556bdb052bed4f2d58f` |
| `b1f4c662306f2d2a522d11e16479544db6f5398f` | `97d86f892112e566cd17b5297beb783fa222aa6e` |
| `a55c366c59a47bbc63d1f4cff965ab3234308f95` | `5a811dcdd95237a57306959ec9f905e46d8a8ed7` |

All 20 original remote branch heads were independently re-read and remain at
their recorded SHAs. No existing PR was changed or closed. A local inventory and
API mapping accompany the bundle; the original branches continue to preserve
their unique work until the planned reconciliation explicitly handles it.

The existing CI push trigger now includes only this named checkpoint branch in
addition to main. Release workflow triggers remain unchanged and main-only/tag
scoped. Golden artifact production remains restricted to main pushes and manual
runs; checkpoint push artifacts cannot become trusted goldens. Formatter failures
emit a nonmutating diff to make correction possible through cloud job logs.
Remove the temporary checkpoint trigger when preparing the final PR178 diff.

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
check outcomes and a complete file inventory (hash, size, mode). ZIP parsing is
bounded and rejects traversal, links, special files and duplicate/case-colliding
paths. The client checks the full Python manifest/lock graph, wheel package,
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

The saved environment runs the dependency-free beta tests, including producer
stage-failure injection, malicious archive cases, wheel identity/RECORD checks,
readback races and repeat consumption. Synthetic wheels/gh responses in those
tests are fixtures; they do not prove an actual wheel was built here. Full
supported-version pytest, Ruff, actionlint, packaging, generated-project/native
checks and an actual prebuild are still blocked by environment downloads.

Local fixture command (Python 3.12.14; this is not supported-runtime acceptance):

```sh
PYTHONPATH=beta/src:src python -m unittest discover -s beta/tests -v
```

At this checkpoint, all **62 tests** pass, including the two-daughter reuse case
and a failure at each of the producer's 14 tool stages. The beta shared-source
manifest check, Python syntax compilation, CI YAML/permission/action-pin inspection
and patch whitespace checks also pass. Stable source, Rust, golden files, root
packaging/lock and release workflows have zero diff from the inspected main.
Ruff is unavailable here, so formatting and lint remain unverified; it must be
run before calling this integration ready.

Existing upstream CI was independently re-read through GitHub: PR175's
[CI 37303024998](https://github.com/benpshore/makeghrepo/actions/runs/37303024998)
is failed; PR177's
[CI 38169735386](https://github.com/benpshore/makeghrepo/actions/runs/38169735386)
and PR178's
[CI 38527335717](https://github.com/benpshore/makeghrepo/actions/runs/38527335717)
are successful at the exact heads listed above. Those results are upstream
evidence, not tests of this checkpoint.

Keep one integration PR: use existing **PR178**, not a new PR. This checkpoint
has not opened, closed or updated any PR. Before reconciling the repository's
existing open PRs, preserve each exact head under a named archive/checkpoint ref,
export its diff and record reviews, CI links and remaining gaps in an integration
inventory. Preserve unrelated release/policy work too; closing a PR is not proof
that its changes landed. Coordinate that preservation and consolidation with the
owner before closing or retargeting existing PRs.

1. Reconcile this checkpoint into PR178's beta foundation, dropping duplicate
   foundation copies and retaining its other reviewed changes only after review.
   Keep stable/Rust rendering unchanged. Run the full existing CI, including the
   new beta-assets and installed coexistence gates, on the exact integration head.
2. Resolve producer/format/integration failures there. A reviewed manual run at
   that exact source is the first consumable artifact; PR candidates remain test
   evidence only. Exercise authenticated artifact retrieval/readback before any
   live daughter work. This checkpoint has no trusted prebuild run yet.
3. Reconcile PR177's required setup/readback/publication ordering with PR175's
   immutable publication-commit validation behind beta's future live path. Add
   persisted remote intent/uncertainty reconciliation, a cloud metadata
   materializer and a verified-source clone gate before enabling live bootstrap.
4. If adopting PR175's stable shared lock template, generate trusted goldens by
   the existing main/manual artifact procedure, validate them completely and
   pass Python/Rust parity. Do not carry its known failing goldens forward or
   silently restrict stable language support.
5. Keep PR145's release refactor and its missing required ci context outside
   this implementation; preserve it in the consolidation inventory for separate
   review. No protected merge, release or live provisioning is authorized here.
