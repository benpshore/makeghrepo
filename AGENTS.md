# Working on makeghrepo

- Submit changes through pull requests. Never push directly or force-push to `main`.
- Keep commits and PR descriptions free of generated-by banners and AI co-author trailers. Do not rewrite existing history to remove them.
- Keep changes focused. Use the shared registry/templates and preserve Python/Rust renderer parity.
- Run heavy compilation and generated-project checks in GitHub Actions, not on the Raspberry Pi development host. Regenerate golden snapshots; never hand-edit them.
- Read `docs/roadmap.md` before extending the platform. Preserve its numbered requirements and distinguish implemented, unmerged, unverified and future work.
- Stable Python lives in `src/makeghrepo`, native Rust in `rust`, and the isolated experimental distribution in `beta`. Do not expose beta entrypoints from the stable distribution or replace an installed stable uv tool.
- Language/module descriptions live in `src/makeghrepo/langs/*.toml`; shared Copier/Jinja templates live in `src/makeghrepo/templates/project`. Keep stable Python/Rust file, byte and executable-bit parity. The beta execution planner is separate; that does not establish orchestration parity.
- Every beta operation must pass the shared planner before effects. Read `docs/recovery.md`; report exact targets and uncertain effects without claiming that an error proves nothing was published.
- Use the existing PRs as integration inputs, with owned checkpoint branches and normal protected-branch review. Never grant owner bypass, enable real auto-merges, release or delete real daughters as part of development tests.
