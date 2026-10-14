# Project platform roadmap

## Product target and scope

Make starting a maintainable, functional project cheap enough that an experiment
can grow into a serious project without rebuilding its foundation. The product
target is a working project of the chosen type in about **ten minutes**, including
useful starter behavior and a clear next development step. That target is not yet
a demonstrated guarantee. A rendered scaffold alone does not satisfy it.

Develop a reliable core and tested feature modules incrementally, learning from
daughter projects. Each module owns its files, checks, dependencies and lifecycle;
the planner resolves compatibility and explains exclusions, including for `--all`.
Preserve existing features while integrating modules through ordinary protected
pull requests. Do not treat an unmerged implementation as absent, nor a template
file as proof its full workflow works.

The order of work is explicit: **26 (beta isolation), then 27/34 (shared planning
and genuine dry-run), then 3/25 (recovery and regression gates)**, before broad new
adapters. The original numbering below is stable so issues and design notes can
reference it. This document records requirements, not permission to run destructive
cleanup, create credentials, release, deploy or enable actual merges.

## Current evidence and integration boundaries

The inspected baseline already contains a shared TOML language registry, Copier
templates, Python and Rust renderers, Docker branches, offline `--render`, resume
markers, a full-stack golden fixture, native distribution checks, release templates,
issue templates, and Spotlight ignores. The existing CLI does not expose an
`--all` macro in this baseline; its all-stack *fixture* is distinct from that CLI
requirement. Python daughter metadata currently requires Python 3.14 and does not
yet satisfy item 11. Root generator runtime requirements are a separate decision.

The beta foundation in this branch adds a separate `makeghrepo-beta` distribution
and entrypoint, shared validation for its supported offline render and dry-run
paths, an inspectable JSON plan, and staging/locking/exception cleanup. The successor checkpoint adds bootstrap dry-run, explicit read-only preflight,
retained offline effect records and read-only status inspection. Actual generation
still requires `--render`; live GitHub creation is refused before effects. This is an
initial implementation of 26/27/34 and bounded local recovery, not completion of
live bootstrap planning, cloud execution or the whole platform. PR #178's original build/coexistence gate passed. The successor's extended
installed gate needs its own supported-runtime verification; prior passes do not
validate later changes.

Relevant open work inspected before this increment:

- [#177: publication gating and readback](https://github.com/benpshore/makeghrepo/pull/177),
  including Python dry-run aliases, capability preflight, origin recovery and retries.
- [#172: input grammar](https://github.com/benpshore/makeghrepo/pull/172) and
  [#173: integration checkpoint](https://github.com/benpshore/makeghrepo/pull/173).
- [#174: uv execution policy](https://github.com/benpshore/makeghrepo/pull/174) and
  [#175: packaged Python lock/bootstrap](https://github.com/benpshore/makeghrepo/pull/175).
- [#142: creation policy controls](https://github.com/benpshore/makeghrepo/pull/142),
  [#145: release verification](https://github.com/benpshore/makeghrepo/pull/145),
  [#147: daughter releases](https://github.com/benpshore/makeghrepo/pull/147), and
  overlapping release-credential work, including
  [#136](https://github.com/benpshore/makeghrepo/pull/136).

These are integration inputs, not changes silently merged into this checkpoint.
Reinspect their current heads and tests before integration. Beta shares baseline
registry/templates without editing them; stable/Rust rendered output must retain
parity. Its separate workflow orchestration is not a claim of Rust execution parity.

## Numbered requirements

| # | Requirement and acceptance boundary |
| --- | --- |
| 1 | A concise `makeghrepo --all` selects all **compatible** options. Encode module dependencies, exclusions, host/runner constraints and mutually exclusive outputs; explain every omission in the plan. The existing all-stack fixture is evidence for those templates only. |
| 2 | Provide a WASM build. Determine the viable runtime, filesystem/network boundaries and supported subset; do not imply native Git/gh operations work unchanged in WASM. |
| 3 | Failed options must not leave a broken daughter repository after reporting failure. Plan transactions, record attempted/confirmed/uncertain effects, retain recoverable work, and perform scoped cleanup. Local exception cleanup is implemented first; remote operations are not globally atomic. See the recovery contract. |
| 4 | Built-in `--CLI` scaffolds a functional CLI with a working command, help, tests and an installation path. Define case/alias policy rather than accidentally conflicting with existing options. |
| 5 | Python uv-tool option with CI/CD: installable command, clean-environment acceptance, locked dependencies and a tested release path. Existing Python console scripts and release templates are a foundation, not proof of every lifecycle. |
| 6 | Periodic upstream Jinja/Copier template updates through Actions. Propose reviewed updates with provenance, compatibility checks and generated diffs; never silently overwrite daughter edits. |
| 7 | macOS swift-cli scaffold with functional behavior, tests, toolchain and installation guidance. Reuse and verify the existing Swift template before adding another. |
| 8 | Daughter rulesets must not require macOS CI. Documentation-only Markdown, text and README edits should not require code CI; retain a coherent required aggregate check so skipped jobs never strand merges. Separate optional platform coverage from required merge policy. |
| 9 | Enable merge commits, squash and rebase; choose a default suitable for large teams with a documented rationale. Existing squash-only/linear-history settings must be reconciled together through reviewed changes, not silently weakened on live repositories. |
| 10 | Restore and retain Docker options. Docker is already present in the inspected registry/templates; investigate reported loss at CLI/distribution/compatibility boundaries and retain every tested Dockerfile branch. |
| 11 | Python daughters support **3.10 and later**, with current Python preferred for development. Validate dependencies, syntax, CI matrices and package metadata. This does not inherit a separate project's Python-3.14-only policy. Generator runtime and daughter compatibility are distinct. |
| 12 | `--tui` creates a functional POSIX Unix/Linux/Darwin terminal app, with input handling, tests and noninteractive behavior. |
| 13 | `--gui` creates a simple working dark-mode GUI with a practical Figma-compatible design workflow; define toolkit and interchange support before promising portability. |
| 14 | `--serve` provides a random-port, localhost-only authenticated web server, with provisioning, secret-reference and Actions integration. Design lifetimes and references without logging secrets. No persistent credentials, access setup or live provisioning without required approval. |
| 15 | Every daughter has a coherent release and version-bump lifecycle with artifact/version consistency checks, installation instructions and recovery from interrupted publication. Existing release templates need ecosystem-by-ecosystem acceptance. |
| 16 | Ignore macOS Spotlight artifacts in templates and enforcement rules. The baseline already includes `.Spotlight-V100`; keep that protection through every distribution and preset. |
| 17 | Update AGENTS.md and Claude guidance with project/template descriptions and simplify README. Preserve essential setup, safety and lifecycle information in linked reference documents. |
| 18 | Improve team/multi-agent concurrency and integration so reviewed work does not accumulate indefinitely in drafts. State actual Git semantics: branches share commits, worktrees isolate working files, commits/checkpoints do not integrate code, and an open PR is not merged work. Assign file/module ownership and a tested integration branch; never bypass main protection or discard open work. |
| 19 | Organization support with authenticated capability checks, permitted visibility and project access. An offline `--owner` label is not authorization or full org support. |
| 20 | Project/issue/epic cleanup leaves no unintended artifacts. Inventory exact IDs and links, plan scope, distinguish owned/new from preexisting/shared objects, and roll back only verified eligible effects. Never delete unrelated or irreplaceable external data. |
| 21 | Name GitHub repositories, Projects, issues, epics, runs and other artifacts intentionally and return their exact canonical links, including on partial failure. |
| 22 | Resolve owner/org safely from authenticated metadata and explicit input, without reading secrets or hardcoding identities. Organize daughter directories by owner then public/private/internal visibility; define migration without moving existing work implicitly. |
| 23 | First-class subtree integration for first- and third-party upstream code. Track source/ref/license/update policy and subtree history; distinguish subtree operations from submodules and ordinary copying. |
| 24 | Keep help current with every implemented feature, aliases, prerequisites, limitations and failure modes. Do not advertise roadmap-only switches as working. |
| 25 | Prevent regressions and lost features using registry coverage, failure injection, no-effect dry-runs, byte/name/executable-bit golden parity, installed distribution checks and lifecycle gates. Preserve existing passing tests. |
| 26 | **FIRST:** separate `makeghrepo-beta` distribution and executable for unstable features. Stable install, command and upgrade route must remain intact. Test simultaneous installation, beta upgrade/uninstall and source/wheel builds in isolated uv tool homes. No beta publishing is enabled by this requirement alone. |
| 27 | **SECOND:** genuine `--dry-run` in help. Validate and report the planned operation without executing it; `--render` writes files and is not dry-run. Offline render dry-run is the first slice; live plans must additionally account for real capability evidence. |
| 28 | Copy-paste examples wrap for tiny phone SSH terminals, avoid excessively long single lines, quote paths/arguments and make placeholder substitutions obvious. |
| 29 | No owner bypass for direct main merges/pushes. Existing baseline owner-bypass behavior requires an explicit reviewed migration that preserves protection and reconciles bootstrap publication; do not apply live policy changes as part of this work. |
| 30 | Auto-commit/auto-merge is a requested feature with visible plans, explicit scope and check-aware execution. Implementing it does not authorize enabling actual merges, bypassing checks or modifying protected branches in this work. |
| 31 | Oh My Zsh integration and optional zshrc/bashrc snippets. Generate inspectable opt-in configuration; do not edit the user's actual shell configuration. |
| 32 | Color output with sensible non-TTY behavior, plain machine-readable output and appropriate color-disable controls. |
| 33 | Did-you-mean suggestions for options and macros. Rank against actual supported inputs without silently executing a guessed command. |
| 34 | Every input resolves through dry-run's shared validation/planning machinery before effects. Execution consumes the resolved plan and revalidates mutable facts, without a less-checked shortcut. Extend the current beta offline boundary to bootstrap, resume, modules and integrations. |
| 35 | C/C++ toolchain/build locking and Actions coverage across aarch64/amd64. Diagnose native headers and cross-compilation limits before work; avoid concurrent writes to shared build directories. |
| 36 | Dismissible suggestions using Escape, plus tab completion and aliases. Define behavior for terminals without interactive capabilities and keep completions synchronized with the registry. |
| 37 | A progress UI that reports meaningful stages rather than raw backend-call noise; retain actionable diagnostics and exact failure/effect details, with clean non-TTY and JSON output. |
| 38 | Better concurrency and serialization. Model dependent setup stages, bound fanout, lock shared outputs and preserve deterministic reporting. A local writer lock does not serialize GitHub or unrelated programs. |
| 39 | HTML/PHP/React/Node support with working starters, tests and lifecycle checks. Existing JavaScript/TypeScript/CSS/Node templates are prior work to retain, not a completed React/PHP adapter. |
| 40 | Swift orchestration in Tart VMs. Validate Apple host availability, guest licensing, images, resource/cost limits and cleanup before scheduling. |
| 41 | Interoperate with ChatGPT Sites, Cloudflare, GitHub Pages and Tailscale Serve/Funnel. Describe public/private reachability and secret references explicitly; adapters do not authorize deployments or persistent access. |
| 42 | Preflight GitHub capabilities **before local repository creation**. Evaluate remote Actions-based uv initialization as a first-class bootstrap path, including auth/permissions, runner availability, quotas/cost, platform support, failure logs and resumable artifacts. A thin phone/SSH client should not need every local toolchain. |
| 43 | Xcode targets for macOS, iPhone, iPad, watch, vision and the requested “HomePad” label. Resolve marketing names to verified SDK/platform support; clarify unsupported/ambiguous targets instead of inventing an SDK. |
| 44 | DMG packaging/self-signing and Apple Developer ID secret-reference automation. Separate local packaging from trusted signing/distribution. No live credential creation, upload, signing identity setup or release without approval. |
| 45 | Cargo toolchain/Cargo.lock, mise and Rust distribution support. Preserve existing Cargo/native release work; verify lock consistency, architectures, checksums and reproducible installation paths. |
| 46 | Ollama/llama.cpp/MCP support. Specify local/remote resources, model provenance, permissions and functional acceptance; do not assume models or GPU resources exist. |
| 47 | PyTorch support with a working tested example and explicit CPU/GPU/platform dependency choices. Diagnose incompatible accelerators and large downloads/costs in the plan. |
| 48 | Preinstallation registry/system-package checks, uv audit and equivalent ecosystem checks. Identify missing development headers, such as Ruby headers for native extensions, before partial installs; do not claim a tool binary alone proves a usable toolchain. |
| 49 | Cocoa/Apple tooling with verified host/SDK requirements, tests and appropriate hosted execution options. |
| 50 | Convenient import into Zed, VS Code, Xcode, Jupyter variants, IntelliJ, Replit, VMs, Docker and VS Code Web. Prefer portable project metadata and explicit opt-in local configuration. |
| 51 | Prefilled issues, epics, todos and Projects templates with named artifacts and exact links. Existing issue templates are useful coverage; remote project creation/cleanup needs separate guarantees. |
| 52 | JSONL/XML support with bounded, safe parsing and schema validation; no untrusted code execution, external entity expansion or executable plan inputs. |
| 53 | Creation manifests/checksums tied to package/release lifecycle. Record inputs, template/source versions and artifact hashes, and state limits: a checksum alone is not authenticity or immutability. Include installation guidance for Rust, Swift and uv daughters. |
| 54 | `-p` alias for `--private`; present in the first beta offline CLI. Keep visibility semantics coherent across every command and implementation. |
| 55 | R/RStudio support with a working project, dependency strategy, tests and lifecycle. Existing R ignore patterns are not an R scaffold. |
| 56 | Opinionated `--website`, `--web-app`, `--sci`, `--db`, `--docker-compose` presets that expand into explicit tested modules. Define compatibility and useful defaults based on daughter experience. |
| 57 | Java support with an explicit build-tool/toolchain choice, functional starter and tests/releases. |
| 58 | Later: a separate uv tool named `gist` accepting quoted/pasted gist content. This is future scope, not permission to publish gists or secrets. Validate publication intent, visibility and content before any future upload. |
| 59 | Daughter `/tests`, `/docs`, `/dev` layout, adapted where ecosystem conventions require a documented mapping. Preserve useful existing layouts through migrations. |
| 60 | Evidence-backed suggestions: identify observations and confidence, distinguish measured compatibility from inference, and make suggestions dismissible rather than applying guesses. |
| 61 | iPhone-friendly tmux support and configuration, with readable narrow-terminal layouts, reconnect behavior and opt-in generated snippets. Do not edit the user's live tmux/shell setup. |

## Measurement and next gates

For each preset, record cold and warm end-to-end elapsed time, downloads and runner
cost assumptions, keystrokes and decisions, prerequisites, a demonstration of
working starter behavior, tests passed/skipped, installation, and one induced
failure plus recovery. Include a narrow phone/SSH terminal and unavailable local
toolchains. Define exactly where timing starts/stops; publishing and remote queue
time cannot be hidden from a ten-minute result.

GitHub is part of the intended execution platform, not just a CI gate after all
local setup succeeds. The remote path needs a capability report and effect journal
before dispatch, pinned workflow/template provenance, bounded runner/VM resources,
stable artifact names/URLs, authenticated artifact retrieval and resumable state.
Unsupported targets, insufficient permissions, missing runners and exhausted
minutes must result in a useful diagnostic and a recoverable plan, without
creating credentials or assuming unlimited remote capacity.

Before beta live creation: integrate reviewed input and bootstrap work; resolve
the no-owner-bypass publication strategy; preflight local versus Actions execution;
implement persisted effect accounting and exact URLs; test uncertain network
outcomes and retries against synthetic services/local disposable Git remotes.
Do not diagnose a historical publication solely from a later 404, a missing local
commit, or a console message. Never exercise cleanup against real daughters.

Promotion gates remain normal PR review and required CI. An integration branch
may gather compatible changes for testing but cannot bypass protected main. A
release-triggering merge is a release action and requires its own authorization.
