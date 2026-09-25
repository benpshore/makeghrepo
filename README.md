# makeghrepo

One command to go from nothing to a new, fully configured GitHub repo. The generated uv Python project comes with CI, CodeQL, Dependabot, branch protection, issue/PR templates, a project board, and muted notifications.

It exists to save keystrokes, and so that AI coding agents can start new repos without broad shell or GitHub admin access.

## Install

```sh
uv tool install git+https://github.com/benpshore/makeghrepo
gh auth refresh -s project,workflow   # once: project boards + pushing workflow files
```

Upgrade with `uv tool upgrade makeghrepo`.

## Use

```sh
ghnew                      # random name like quiet-otter, asks once to confirm
ghnew my-thing -y          # named, no prompt (what an AI agent should run)
ghnew my-thing --private -d "what it does"
ghnew --local -y           # everything except GitHub
makeghrepo configure OWNER/REPO   # (re)apply GitHub settings; safe to re-run after a failure
```

`ghnew` is shorthand for `makeghrepo new`. Defaults: the project goes in `~/code/GitHub/<name>` (override with `--dir` or `MAKEGHREPO_DIR`), and the owner is your gh user (override with `--owner` or `MAKEGHREPO_OWNER`).

## What `new` does

1. Picks a name: yours (normalized to `lower-dashes`), or a random adjective-noun pair not already used locally or on GitHub.
2. Renders the copier template in `src/makeghrepo/templates/python/`.
3. Smoke tests the new project: `uv lock`, `uv sync --locked`, `ruff format --check`, `ruff check`, `pytest`, `uv audit`, `uv build`. If any fail, it stops and nothing is published.
4. Runs `git init -b main`, `git add --all`, `git commit -m setup` (via GitPython).
5. Runs `gh repo create --source . --remote origin`, which creates the repo empty.
6. Configures the repo through `gh api`. Each step is idempotent and reported ✓/✗; failures don't stop the others:
   - squash-merge only, auto-merge on, delete branches after merge, wiki off
   - Dependabot alerts and security fixes
   - public repos only: secret scanning and push protection, private vulnerability reporting
   - **pushes `main`**, after push protection is on and before the ruleset
   - public repos only: a `protect-main` ruleset. PRs are required (0 approvals, because you can't approve your own PR), the `ci` check from GitHub Actions must pass, history stays linear, and force-push and deletion are blocked.
   - labels `epic` and `task`
   - notifications set to **Ignore** for the repo, and no CODEOWNERS file, so nothing pings you
   - a GitHub Project with the same name, linked to the repo (needs the `project` scope)

With `--private` on GitHub Free, rulesets, secret scanning and code scanning aren't available. makeghrepo skips them and leaves CodeQL out of the project. CI still runs but can't block merges, so the local smoke test is the real gate.

If the push fails, run `git -C <dir> push -u origin main` and then `makeghrepo configure OWNER/REPO`.

### Releases

Bump the version with `uv version --bump patch`, merge the PR, then run `git tag v$(uv version --short) && git push --tags`. The `release` workflow checks that the tag matches the version, tests, runs `uv audit`, runs `uv build`, and publishes a GitHub Release with the wheel and sdist.

### What's in the generated project

`pyproject.toml` (uv_build, ruff, pytest), `src/` layout package with a CLI entry point, a smoke test, `.gitignore` (Python, macOS `.DS_Store`, every common database/data file, secrets, editors, caches), `.editorconfig`, MIT `LICENSE`, `SECURITY.md`, `AGENTS.md` and `CLAUDE.md`, and under `.github/`:

| File | Purpose |
|---|---|
| `workflows/ci.yml` | `uv sync --locked`, format check, lint, test, `uv audit`, build. The job name `ci` is the required check. |
| `workflows/release.yml` | on a `v*` tag: test, audit, `uv build`, then `gh release create` with the wheel and sdist |
| `workflows/codeql.yml` | public repos only: CodeQL (python + actions, security-extended queries), weekly and on every PR |
| `dependabot.yml` | weekly grouped updates for uv and GitHub Actions (pinned by SHA), with a 7-day cooldown |
| `pull_request_template.md` | what / how to verify / notes |
| `ISSUE_TEMPLATE/` | bug, feature/task, epic (epics use native sub-issues) |

## Develop

```sh
uv sync
uv run ruff format && uv run ruff check
uv run pytest              # includes a slow end-to-end test (needs network)
uv run pytest -m "not slow"
uv run makeghrepo new --local -y --dir /tmp/x
```

## Roadmap

- [ ] More copier templates: rust, swift, docker, shell, js, tart vm, css
- [ ] Orchestrating shell scripts and make/just files from Python
- [ ] Optional Dependabot auto-merge for patch updates
- [ ] Optionally open a tmux window in the new project (for now `new` prints the command)
