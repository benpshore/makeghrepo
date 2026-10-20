# Working on makeghrepo

- Submit changes through pull requests. Never push directly or force-push to `main`.
- Keep commits and PR descriptions free of generated-by banners and AI co-author trailers. Do not rewrite existing history to remove them.
- Keep changes focused. Use the shared registry/templates.
- Run heavy compilation and generated-project checks in GitHub Actions, not on the Raspberry Pi development host. Regenerate golden snapshots; never hand-edit them.
- Read `docs/roadmap.md` before extending the platform. Preserve its numbered requirements and distinguish implemented, unmerged, unverified and future work.
- Stable Python lives in `src/makeghrepo` and the isolated experimental distribution in `beta`. Do not expose beta entrypoints from the stable distribution or replace an installed stable uv tool.
- Language/module descriptions live in `src/makeghrepo/langs/*.toml`; shared Copier/Jinja templates live in `src/makeghrepo/templates/project`. Golden snapshots pin rendered file names, bytes and executable bits. The beta execution planner is separate; that does not establish orchestration parity.
- The Rust implementation is parked on the `rust` branch (see #150). Python still generates Rust daughter projects: keep `langs/rust.toml` and the Rust templates.
- Every beta operation must pass the shared planner before effects. Read `docs/recovery.md`; report exact targets and uncertain effects without claiming that an error proves nothing was published.
- Use the existing PRs as integration inputs, with owned checkpoint branches and normal protected-branch review. Never grant owner bypass, enable real auto-merges, release or delete real daughters as part of development tests.
