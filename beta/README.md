# makeghrepo-beta

An independent, experimental Python distribution and executable. Install it with
`uv tool install ./beta` from a checkout. It bundles this checkout's shared
registry, Copier templates and renderer; it does **not** depend on or upgrade the
`makeghrepo` distribution, and exports only `makeghrepo-beta`. Keep it in its own
uv tool environment; do not install both distributions into one Python environment
because both carry the shared `makeghrepo` import package.

The offline path supports transactional **offline generation**. It creates
the same starter files as stable `--render`, with the same fixed preview defaults.
GitHub creation and publication remain unavailable in beta. The separate `cloud`
path plans and consumes prebuilt assets using authenticated, read-only GitHub API
calls. It performs no local daughter dependency installation or checks.
On the offline path, omitting `--render` fails before effects,
including with `--dry-run`. Existing stable and Rust commands retain their behavior.

```sh
uv tool install ./beta
makeghrepo-beta --help
makeghrepo-beta sample python \
  --render ./preview \
  --dry-run
makeghrepo-beta sample python \
  --render ./preview
```

`--dry-run` (`-n`) uses the same validator and plan as execution, but does not create
project directories, lock files, Git data or remote artifacts and does not start
subprocesses. `--json` emits the plan for inspection and requires `--dry-run`.
It is an output format, not an executable saved-plan input. Hidden stable preview
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

This is exception recovery, not a durable filesystem transaction: abrupt process
termination or power loss may leave staging/lock directories. Never remove a lock
without verifying its writer stopped. Other programs do not honor beta's lock;
revalidation detects ordinary changes but cannot promise atomicity against hostile
concurrent filesystem modification. Rendered code has not been run or audited.

The beta version is a separate prerelease version; no beta release or publishing
workflow is enabled. Upgrade a local checkout with `uv tool install --reinstall
./beta`; this targets only beta. Building an sdist bundles the shared sources so a
wheel can be rebuilt without the checkout. CI verifies stable/beta coexistence,
beta uninstall, source-distribution rebuilding, and installed render parity.

`shared-files.json` explicitly lists the shared payload, so build hooks never sweep
untracked files or secrets from a development checkout into the beta artifact.
After staging new shared package files, run `python scripts/regen-beta-sources`
from the repository root and review the manifest diff. CI checks it against tracked
files. Missing files, unsafe paths and source symlinks fail the build. The sdist
carries the same manifest and payload.

For the beta cloud contract, identity limits, recovery, test evidence and
integration order, see [cloud assets](../docs/cloud-assets.md). Historical checkpoint
CI exercised the actual producer and installed coexistence; the combined PR must
pass those gates again. Live daughter provisioning remains disabled.

See [the complete roadmap](../docs/roadmap.md) and
[recovery contract](../docs/recovery.md). The ten-minute working-project target is
a product goal requiring measured starter behavior, checks and recovery per preset;
offline rendering or prepared assets alone do not establish it.

```sh
makeghrepo-beta cloud plan \
  --source "$REVIEWED_SOURCE_SHA" \
  --owner example --name sample
makeghrepo-beta cloud fetch \
  --source "$REVIEWED_SOURCE_SHA" \
  --owner example --name sample \
  --run "$SUCCESSFUL_TRUSTED_CI_RUN_ID" \
  --destination ./prepared-assets \
  --dry-run
```

The source must be a reviewed full commit SHA, with a successful main-push or
manual CI run that includes the prebuild producer. Existing main has no such
producer yet. Dry-run checks local inputs and reports remote state as unverified;
remove `--dry-run` to fetch and verify that existing result. The output is a
prepared asset directory, not a created or cloned Git repository. Its package
is always `makeghrepo-seed`; the daughter owner/name exist only in `identity.json`.
