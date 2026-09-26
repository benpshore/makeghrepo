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
```

Languages: `python` `rust` `swift` `js` `css` `c` `cpp` `objc` `objcpp` `api` `postgres` `sql` `docker` `shell`. Aliases like `c++`, `objc++`, `rest` and `pg` also work.

If anything fails, fix it and run the same command again. The repo already exists, so makeghrepo skips creating it and only finishes what's missing: pushing `main` and re-applying settings. Every setting is safe to re-apply.

Projects go in `~/code/GitHub/<name>`. Set `MAKEGHREPO_DIR` to use another folder.

No git identity or GitHub auth setup needed beyond `gh auth login`: makeghrepo falls back to a `users.noreply.github.com` commit identity if none is configured, and never touches your global git config.

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
- `codeql.yml` (public repos): actions, plus python, js, rust, c-cpp and swift as chosen.
- `dependabot.yml`: GitHub Actions, plus uv, cargo, swift, npm, docker and docker-compose as chosen; weekly and grouped, with a 7-day cooldown.
- `release.yml`: push a `v*` tag and it publishes a GitHub Release. For Python it first checks that the tag matches the version, tests, runs `uv audit` and `uv build`, and attaches `dist/*`.

| language | files | checks (locally and in CI) |
|---|---|---|
| python | `pyproject.toml`, `src/<pkg>/`, `tests/` | ruff format + check, pytest, `uv audit`, `uv build` |
| rust | `Cargo.toml`, `src/main.rs` | `cargo fmt --check`, `clippy -D warnings` (pedantic), `cargo test` |
| swift | `Package.swift`, `Sources/`, `Tests/` | `swift build`, `swift test` |
| js | `package.json`, `eslint.config.js`, `src/`, `test/` | eslint, `node --test`, `npm audit` (CI) |
| css | `styles/`, `.stylelintrc.json` | stylelint |
| c, cpp, objc, objcpp | one `CMakeLists.txt`, `src/main.{c,cpp,m,mm}` | CMake build with `-Wall -Wextra -Werror`, ctest (macOS runner if ObjC) |
| api | `openapi.yaml` | Spectral |
| postgres | `compose.yaml`, `db/migrations/` | CI applies the migrations to a real Postgres 17 |
| sql | `sql/`, `.sqlfluff` | sqlfluff (Postgres dialect with `postgres`) |
| docker | `Dockerfile` for your language, `.dockerignore` | hadolint, `docker build` (CI) |
| shell | `scripts/hello.sh` | shellcheck |

## Develop

```sh
uv sync
uv run ruff format && uv run ruff check
uv run pytest              # includes slow end-to-end tests for python and rust (network)
uv run pytest -m "not slow"
```

To add a language: add its files to `templates/project/template/` behind a `[% if flag %]` name, a flag in `copier.yml`, its jobs in `ci.yml.jinja` and `codeql`/`dependabot`, its line in `_checks.jinja`, and its entries in `LANGUAGES` and `CHECKS` in `scaffold.py`.
