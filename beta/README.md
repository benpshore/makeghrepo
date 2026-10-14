# makeghrepo-beta

An independent, experimental Python distribution and executable. Install it with
`uv tool install ./beta` from a checkout. It bundles this checkout's shared
registry, Copier templates and renderer; it does **not** depend on or upgrade the
`makeghrepo` distribution, and exports only `makeghrepo-beta`. Keep it in its own
uv tool environment; do not install both distributions into one Python environment
because both carry the shared `makeghrepo` import package.

Beta supports transactional **offline generation**, bootstrap previews, explicit
read-only GitHub preflight, and recovery-record inspection. Offline files match
stable `--render`, with the same fixed preview defaults. Live GitHub creation,
dependency execution, Actions dispatch and publication remain unavailable.
Existing stable and Rust commands retain their behavior.

```sh
uv tool install ./beta
makeghrepo-beta --help
makeghrepo-beta sample python -p \
  --dry-run
makeghrepo-beta sample python -p \
  --preflight --json
makeghrepo-beta sample python \
  --render ./preview \
  --dry-run
makeghrepo-beta sample python \
  --render ./preview
makeghrepo-beta --status ./preview
```

`--dry-run` (`-n`) uses the same validator and plan as execution, but does not create
project directories, lock files, Git data or remote artifacts and does not start
subprocesses. `--json` emits the dry-run plan for inspection.
Bootstrap previews require an explicit project name. Without `--owner`, plain
bootstrap dry-run leaves authenticated owner and destination unresolved. `--root`
(or `MAKEGHREPO_DIR`, default `~/code/GitHub`) sets a proposed bootstrap root;
resolved destinations use `OWNER/public/NAME` or `OWNER/private/NAME`. This does
not move stable projects or implement organization creation.

`--preflight` is a separate read-only observation mode: it invokes `gh api` GET
requests for account and target metadata, with the existing gh authentication.
It creates no project, journal, credentials or settings. Missing/inaccessible
repositories and unknown rights stay unknown; name availability is not reserved.
Existing-repository permissions do not establish creation rights, runner capacity
or quota. No token values, authentication headers or raw backend errors are
reported. These observations are not authorization for future execution.
`--preflight` cannot be combined with `--dry-run` or `--render`.

`--json` also works with `--preflight` and `--status`. It is an output format,
not an executable saved-plan input. A successful inspection exits zero even if
capabilities are unknown; `execution_available` remains false for bootstrap.
Help/prose adapt to 32-column terminals. Paths/URLs remain intact on separate
lines, and pipes/JSON contain no ANSI progress sequences. Hidden stable preview
metadata options are visible here: `--owner`, `--author`, `--description`, `--year`.
`--private` (`-p`), `--lib` and explicit `--license MIT` work as in offline stable
generation. Languages and aliases come from the shared registry.
Options can be interleaved with the positional name/languages. Abbreviated long
options are refused, so a partial or mistyped option cannot silently select an
operation.

Execution revalidates the destination, serializes beta writers with an exclusive
directory lock, renders in a private sibling staging directory and promotes the
complete render with a same-filesystem rename. A failure before promotion
preserves the previous absent/empty destination and removes only owned staging
paths and empty parents. If an interrupt/error arrives immediately after rename,
readback distinguishes installed output from an uncertain outcome; completed
output is retained. Every execution failure names the phase and destination.
Cleanup failures list exact retained paths instead of claiming rollback succeeded.
Existing nonempty directories,
symlinks, and changes since planning are refused. No existing daughter is deleted.

Execution first writes a private, versioned recovery record in the nearest
existing destination ancestor recorded by the plan. Its name contains a
hash of the destination and a unique invocation ID. The record is outside the
rendered project and remains after success or failure. Retries retain earlier
records. Updates use a flushed temporary file, atomic rename and parent-directory
flush. Failures can also leave a named `.json.pending` snapshot.

`--status DIR` discovers those records in destination ancestors without writing,
replaying, cleaning or removing locks. It distinguishes the recorded outcome
from the current directory-identity observation. Invalid, oversized, truncated
or symlinked records are reported as unreadable; they are never executed. These
records are untrusted local evidence, not signatures or authority to delete.
Review any retention cleanup manually outside this command.

This is not a power-loss transaction: generated files themselves are not fsynced,
and filesystems differ in durability and atomic-rename guarantees. Process-exit
tests do not simulate every storage failure. Abrupt termination may leave
staging/lock directories. Never remove a lock
without verifying its writer stopped. Other programs do not honor beta's lock;
revalidation detects ordinary changes but cannot promise atomicity against hostile
concurrent filesystem modification. Rendered code has not been run or audited.

The beta version is a separate prerelease version; no beta release or publishing
workflow is enabled. Upgrade a local checkout with `uv tool install --reinstall
./beta`; this targets only beta. Building an sdist bundles the shared sources so a
wheel can be rebuilt without the checkout. CI verifies stable/beta coexistence,
beta uninstall, source-distribution rebuilding, and installed render parity.
The extended installed gate also covers bootstrap preview, preflight with a fake
gh executable, and recovery inspection; no live GitHub mutations are used.

`shared-files.json` explicitly lists the shared payload, so build hooks never sweep
untracked files or secrets from a development checkout into the beta artifact.
After staging new shared package files, run `python scripts/regen-beta-sources`
from the repository root and review the manifest diff. CI checks it against tracked
files. Missing files, unsafe paths and source symlinks fail the build. The sdist
carries the same manifest and payload.

See [the complete roadmap](../docs/roadmap.md) and
[recovery contract](../docs/recovery.md). The ten-minute working-project target is
a product goal requiring measured starter behavior, checks and recovery per preset;
offline rendering alone does not establish it.
