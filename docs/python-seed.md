# Packaged Python bootstrap lock

The Python CLI creates a populated repository without resolving, installing or
executing daughter dependencies on the user's account. Actual Copier/Jinja
renders the packaged lock. Jinja raw blocks preserve uv's `[[package]]` tables;
the one substitution is the editable root's validated project name. Python and
Rust still render the same shared templates. Rust bootstrap behavior is unchanged.

Supported bootstrap selections are Python, with optional SQLite/API components,
in console or library layout, public or private, with no license or explicit MIT;
language-neutral creation also remains supported. Other selections fail before
creating a directory or remote and remain available for offline rendering.

`seedlock.py` parses the manifest/lock without subprocesses and compares their
normalized content with `data/python-seed.json`. The complete dependency graph,
versions, indexes, URLs and hashes must match. Manifest normalization permits
only the template's identity/layout/license fields; changed requirements,
sources, build settings, hooks or extra configuration are rejected. Missing and
symlinked inputs fail. On an unpublished retry, committed Python inputs must
also match the validated files. A published-repository configuration retry does
not validate subsequent manifest edits or push subsequent commits.

This is a bounded template contract, not an arbitrary resolver or OS sandbox.
Git and gh still use the user's existing authentication and configuration.
Same-user files, processes, services, sockets and Git hooks/helpers remain outside
this change's isolation claims. The seed pins the project dependency graph;
PEP 517 build requirements remain the template's existing ranges. Later explicit
sync/build commands execute code and may resolve those build requirements.

## Provenance and updates

The initial seed was generated and tested in trusted tool CI with uv 0.12.23 and
CPython 3.14.8, not copied from a user's project:

- Source: `7331ea49ec673fefc635afa676fb8ecf328e6fdd`.
- Run: https://github.com/benpshore/makeghrepo/actions/runs/37297386456.
- Artifact: `python-seed-lock-feasibility`, ID `11340426390`;
  archive digest `sha256:e72542cec969ad0eeff99338cb85a10f9f9a93ba301df1c48e42b5c53b21e59e`.
- Packaged template and original seed digests, manifest digest, tool versions,
  source commit, run ID and both tested project names are recorded in the contract.

The public template bytes were recovered from that successful job's explicitly
emitted base64 data and checked against the recorded template and seed SHA-256
values. The artifact archive was not downloaded. These hashes record integrity
and provenance; they do not authenticate a compromised builder or maintainer.

For an intentional update, use a trusted development/CI environment with the
reviewed source and chosen uv/Python versions:

```sh
uv run scripts/regen-python-lock --out out/python-seed-candidate
```

This command executes resolution, install, lint, tests and builds in that trusted
environment. It writes candidate template/contract/provenance files to a new
output directory; it never replaces packaged files or snapshots. Its fresh PyPI
resolution is deliberately confined to maintenance. Review dependency changes,
hashes, source/run/tool versions and the two-name fresh-resolution comparisons,
then import the candidate template and contract together in a focused PR. No
runtime downloads or automatic seed updates occur during repository creation.

CI checks the packaged lock with `uv lock --check --offline` using an empty cache
for both names, both layouts and all supported Python/component selections. It
also tests changed manifests/locks, absent/symlinked locks, unsupported stacks,
real local bootstrap commits with mocked GitHub, and published-repository retries.
Required full CI and Python/Rust render parity remain enabled.

Adding the lock changes every Python golden snapshot. Follow the existing README
golden policy: regenerate in a trusted main/manual CI run, validate the complete
artifact in staging with `scripts/regen-golden --check`, and only then replace
snapshots. PR artifacts are not a trusted golden source. This draft leaves those
snapshots unchanged pending an authorized trusted run; the golden failures remain
merge blockers. The seed validation workflow does not upload golden artifacts.
