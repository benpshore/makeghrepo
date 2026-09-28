# Working on makeghrepo

- Submit changes through pull requests. Never push directly or force-push to `main`.
- Keep commits and PR descriptions free of generated-by banners and AI co-author trailers. Do not rewrite existing history to remove them.
- Keep changes focused. Use the shared registry/templates and preserve Python/Rust renderer parity.
- Run heavy compilation and generated-project checks in GitHub Actions, not on the Raspberry Pi development host. Regenerate golden snapshots; never hand-edit them.
