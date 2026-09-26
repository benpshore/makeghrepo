# Performance issues

`makeghrepo` is noticeably slower than expected for a repository bootstrap utility.

The current evidence points to wall-clock latency from synchronous external work, not Python execution or Copier rendering.

## Main causes

### 1. Local validation is on the critical path

`scaffold.smoke_test()` runs substantial local validation before repository setup completes.

For Python this includes:

```text
uv lock
uv sync --locked
ruff format --check
ruff check
pytest
uv audit
uv build
```

Other language paths may invoke Cargo, npm, CMake, npx, hadolint, shellcheck, or other external tools.

Several of these checks are repeated later in GitHub Actions. The bootstrap path should distinguish between work required to produce a valid initial repository and validation that CI can perform after the repository is pushed.

Tracked in #9.

### 2. GitHub configuration is mostly serialized

`github.configure_all()` currently performs many independent operations sequentially:

```text
repo settings
dependabot alerts
dependabot security fixes
private vulnerability reporting
push main
ruleset lookup/create/update
label creation
notification subscription
project lookup/create/link
```

Each `gh()` call starts a new subprocess and usually performs a separate network round trip.

The main performance problem is that independent operations follow a request → wait → request → wait pattern.

Tracked in #10.

### 3. Project-board lookup is expensive and blocks completion

`configure_project()` currently uses:

```sh
gh project list --owner <owner> --closed --limit 1000 --format json
```

and scans the returned Projects locally.

Fetching up to 1,000 Projects for every bootstrap is potentially expensive, and Project setup is not required before the repository itself is usable.

Tracked in #11.

## Ordering constraints

Do not simply run everything concurrently. Preserve these dependencies:

1. The GitHub repository must exist before repository-scoped configuration.
2. Security/push-protection settings intended to protect the first push must be enabled before that push.
3. The initial `main` push must occur before installing the ruleset that requires PRs/status checks.
4. If a Project does not exist, Project creation must precede linking it to the repository.

Most other GitHub configuration work can be evaluated for concurrent execution.

## Target execution shape

Conceptually:

```text
render template
generate only required local artifacts
git init / commit
create GitHub repo
        |
        +-- settings
        +-- Dependabot/security configuration
        +-- labels
        +-- notification settings
        +-- Project lookup/setup
        +-- required pre-push protection
                 |
                 v
              push main
                 |
                 v
           create/update ruleset
```

The exact implementation should stay simple. Avoid introducing a large async/orchestration framework solely for this optimization; bounded standard-library concurrency is preferable if sufficient.

## Definition of repo readiness

For performance purposes, a repository is effectively ready when:

- the local repository exists,
- the initial commit exists,
- the GitHub repository exists,
- protection required before the first push is active,
- `main` has been pushed,
- protection that must follow the first push is installed.

Labels, notification preferences, and Project-board setup should not unnecessarily delay reaching that state, even if the process still completes them before exit.

## Measurement

Performance changes should be measured rather than inferred.

Track timings for at least:

- user/repository discovery,
- template rendering,
- local smoke/lockfile work,
- git init/commit,
- GitHub repository creation,
- GitHub configuration,
- initial push,
- ruleset setup,
- Project setup.

Record before/after wall-clock timings for:

- language-neutral repository,
- Python repository,
- Rust repository when practical.

Tracked in #12.

## Preserve

Performance work must not break:

- idempotent re-runs,
- resume-after-failure behavior,
- first-push protection,
- ruleset ordering,
- public/private repository differences,
- `MAKEGHREPO_SKIP_LOCAL_CHECKS`,
- required lockfile generation,
- existing test coverage.

## Epics

- #9 Performance: reduce local bootstrap critical path
- #10 Performance: parallelize independent GitHub configuration
- #11 Performance: remove Project board lookup from the hot path
- #12 Performance: instrument and benchmark repository bootstrap
