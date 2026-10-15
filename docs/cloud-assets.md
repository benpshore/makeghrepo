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
