# makeghrepo

One command creates a new, fully configured GitHub repo: public by default, and ready for any language or the ones you name.

It exists to save keystrokes, and so that AI coding agents can start new repos without broad shell or GitHub admin access.

## Install

```sh
uv tool install git+https://github.com/benpshore/makeghrepo
gh auth refresh -s project,workflow   # once: project boards + pushing workflow files
```

## Use

```sh
makeghrepo                          # random name (e.g. quiet-otter), language-neutral
makeghrepo quiet-otter              # named, language-neutral
makeghrepo quiet-otter python       # one language
makeghrepo quiet-otter rust docker  # several
makeghrepo swift                    # first word is a language, so the name is random
makeghrepo quiet-otter --private
makeghrepo quiet-otter python --lib # library layout, no console script (like `uv init --lib`)
```

Languages: `python` `rust` `swift` `js` `css` `c` `cpp` `objc` `objcpp` `api` `postgres` `sql` `docker` `shell` `go`. Aliases like `c++`, `objc++`, `rest`, `pg` and `golang` also work.

If anything fails, fix it and run the same command again. The repo already exists, so makeghrepo skips creating it and only finishes what's missing: pushing `main` and re-applying settings. Every setting is safe to re-apply.

makeghrepo only resumes folders it created itself. It records that, along with the visibility and languages you chose, in `.git/makeghrepo.json`, which is never committed. It refuses any other git repo at that path, so an unrelated local project can't be published by accident. A re-run keeps the original visibility and languages. `--private` on a repo created public is refused rather than ignored, and makeghrepo never changes an existing repo's visibility.

Projects go in `~/code/GitHub/<name>`. Set `MAKEGHREPO_DIR` to use another folder.

No git identity or GitHub auth setup needed beyond `gh auth login`: makeghrepo falls back to a `users.noreply.github.com` commit identity if none is configured, and never touches your global git config.

Local checks run third-party code: npm packages, pinned `npx`/`uvx` tools, and the new project's own tests. They run with credential *channels* removed from the environment: no `GH_*`/`GITHUB_*` variables, no keyring session, no ssh agent. That is not isolation: a check can still read files under your home directory, including `gh`'s stored token (#113 tracks an OS-level sandbox). Until then, treat a generated project's checks like any code you run by hand. npm installs use `--ignore-scripts`. Checks that can't work on this machine are skipped and left to CI, which runs them on GitHub:

- ObjC/ObjC++ need macOS.
- Docker needs a daemon that answers `docker info`.
- `cargo fmt`/`clippy` need their rustup components.

Before the first commit, makeghrepo refuses to commit anything CI would reject: `.env` files, private keys, databases, OS junk. Rust needs a project name that starts with a letter.

On a constrained host (no cooling, a minimal CI runner) set `MAKEGHREPO_SKIP_LOCAL_CHECKS=1` to skip local lint/test/build even for tools that are installed — CI runs the same checks anyway. Lockfiles (`uv.lock`, `package-lock.json`) are still generated locally, since CI and the Dockerfiles depend on them.

## What it does

1. Renders one copier template (`src/makeghrepo/templates/project/`). The shared base is always included; each language you name adds its own files.
2. Runs each language's lint, test and build locally, skipping any tool that isn't installed (CI still runs it). If a check fails, nothing is published.
3. Runs `git init -b main`, `git add --all`, `git commit -m setup`.
4. Creates the GitHub repo empty, then configures it:
   - squash-merge only, auto-merge on, delete branches after merge, wiki off
   - Dependabot alerts and security fixes
   - public repos: secret scanning, push protection, private vulnerability reporting
   - **pushes `main`**, after push protection is on
   - public repos: a `protect-main` ruleset. PRs are required (0 approvals, because you can't approve your own PR), the `ci` check from GitHub Actions must pass, history stays linear, and force-push and deletion are blocked.
   - labels `epic` and `task`, and a Project board linked to the repo
   - notifications set to **Ignore**, and no CODEOWNERS file, so nothing pings you

**Private repos on GitHub Free** can't have rulesets, secret scanning or code scanning, so makeghrepo skips them and leaves CodeQL out. CI still runs but can't block merges; the local checks are the gate.

## What every repo gets

- `.gitignore` covering every supported language, plus `.DS_Store`, databases and data files, secrets, and editor/cache files. A `repo` CI job fails if any of those get committed anyway.
- `README.md` and `AGENTS.md` (plus `CLAUDE.md`), each listing that project's check commands; MIT `LICENSE`, `SECURITY.md`, `.editorconfig`.
- Issue templates for bug, task and epic (epics use native sub-issues), and a PR template.
- `ci.yml`: one job per language, plus a final `ci` job that passes only if all of them passed. That `ci` job is the one required check.
- `codeql.yml` (public repos): actions, plus python, js, rust, go, c-cpp and swift as chosen.
- `dependabot.yml`: GitHub Actions, plus uv, cargo, gomod, swift, npm, docker and docker-compose as chosen; weekly and grouped, with a 7-day cooldown.
- `release.yml`: push a `v*` tag and it publishes a GitHub Release. For Python the version *is* the tag (hatchling + uv-dynamic-versioning, nothing to bump by hand): it checks the tag against the computed version, tests, runs `uv audit` and `uv build`, and attaches `dist/*`. For Rust it checks the tag against `Cargo.toml`, tests, builds a release binary and attaches it with a `SHA256SUMS` file. For Go it tests and attaches static linux amd64 and arm64 binaries with `SHA256SUMS`.
- `auto-release.yml` (public Python repos): every merge to `main` waits for that commit's `ci` check, then tags the next minor version and publishes the release. The same design makeghrepo itself uses.

| language | files | checks (locally and in CI) |
|---|---|---|
| python | `pyproject.toml` (version from git tags), `src/<pkg>/`, `tests/` | ruff format + check, pytest, `uv audit`, `uv build` |
| python --lib | same, minus the console script; adds `py.typed` | same checks |
| rust | `Cargo.toml`, `src/main.rs` | `cargo fmt --check`, `clippy -D warnings` (pedantic), `cargo test` |
| swift | `Package.swift`, `Sources/`, `Tests/` | `swift build`, `swift test` (CI runs them in the pinned `swift:6.4.0-noble` image, so the Ubuntu 26.04 runner change can't break them) |
| js | `package.json`, `eslint.config.js`, `src/`, `test/` | eslint, `node --test`, `npm audit` (CI) |
| css | `styles/`, `.stylelintrc.json` | stylelint |
| c, cpp, objc, objcpp | one `CMakeLists.txt`, `src/main.{c,cpp,m,mm}` | CMake build with `-Wall -Wextra -Werror`, ctest (macOS runner if ObjC) |
| go | `go.mod`, `main.go`, `main_test.go` | `go vet`, `go test` (CI adds `gofmt -l` and `-race`); distroless static Docker runtime; release builds linux amd64 + arm64 binaries |
| api | `openapi.yaml` | Spectral |
| postgres | `compose.yaml` (db service, `pgdata` volume), `db/migrations/` | CI applies the migrations to a real Postgres 17 |
| sql | `sql/`, `.sqlfluff` | sqlfluff (Postgres dialect with `postgres`) |
| docker | `Dockerfile` for your language (non-root, `/data` volume), `compose.yaml` (app service, `appdata` volume), `.dockerignore` | hadolint, `docker build` (CI) |
| shell | `scripts/hello.sh` | shellcheck |

## Rust build

`rust/` holds a Rust port of the same tool: same template, same registry, same steps, and it must render every `tests/golden` combo byte for byte (CI's `rust` job checks). Every release attaches prebuilt binaries for Linux x86_64, Linux aarch64 (Raspberry Pi) and macOS arm64, each with a `.sha256` file:

```sh
v=$(gh release view --repo benpshore/makeghrepo --json tagName --jq .tagName)
gh release download "$v" --repo benpshore/makeghrepo -p "makeghrepo-aarch64-unknown-linux-gnu*"
shasum -a 256 -c makeghrepo-aarch64-unknown-linux-gnu.sha256
install -m 755 makeghrepo-aarch64-unknown-linux-gnu ~/.local/bin/makeghrepo
```

The Rust binary adds two offline modes: `makeghrepo NAME LANG... --render DIR` renders the project into `DIR` and stops (no checks, no git, no GitHub), and `--snapshot [--combo NAME]` prints the rendered project in golden-snapshot format. Nothing in this repo compiles Rust on a development machine: CI builds, tests and checks it.

## Develop

```sh
uv sync
uv run ruff format && uv run ruff check
uv run pytest              # includes slow end-to-end tests for python and rust (network)
uv run pytest -m "not slow"
```

### Cloud checks

makeghrepo's own CI does all the real verification on GitHub runners; nothing needs to run on a dev machine.

- **`test`:** ruff, the full pytest suite, and the golden snapshots.
- **`templates`:** renders every combo in `tests/golden_snapshots.py` and runs the generated project's own local checks on real toolchains. ObjC combos run on macOS.
- **`actionlint`:** lints makeghrepo's workflows and every generated one.
- **`ci`:** rolls all of the above up into the one required check.

### Golden snapshots

`tests/golden/` holds the exact rendered output of a fixed set of language combos (listed in `tests/golden_snapshots.py`). `tests/test_golden.py` fails on any difference in a file's bytes, name or exec bit, so a refactor that shouldn't change output can prove it doesn't. When you change templates on purpose, regenerate and review the diff:

```sh
uv run scripts/regen-golden
git diff tests/golden
```

The multi-language combos include every Dockerfile branch (`python+docker`, `js+docker`, `rust+docker`, `python+rust+docker`) and `all`, which CI checks three times in a row to flush out ordering races between concurrent checks.

Never edit or hand-merge a `.golden` file. On a conflict, rebase and regenerate. CI also renders them on every run and uploads them as the `golden` artifact, so they can be regenerated without a local toolchain:

```sh
gh run download <run-id> -n golden -D tests/golden
```

To add a language or component, add only files it owns:

- `src/makeghrepo/langs/<id>.toml`: its registry entry (aliases, local checks, CI job, Dependabot ecosystem, CodeQL language, npm scripts). `src/makeghrepo/registry.py` documents every field.
- `src/makeghrepo/templates/project/_ci/<job>.jinja`: its job in the generated `ci.yml`.
- `src/makeghrepo/templates/project/_checks/<job>.jinja`: its line in the README and AGENTS checks list.
- Its project files under `templates/project/template/`, behind `[% if '<id>' in languages %]` names.

Then regenerate the golden snapshots, and review the diff: every new token gets its own snapshot automatically.
