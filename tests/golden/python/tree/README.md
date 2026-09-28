# quiet-otter

quiet-otter

## Checks

Run these before opening a PR. CI runs the same ones.

```sh
uv run ruff format && uv run ruff check && uv run pytest && uv audit --preview-features audit-command
```

`main` is protected: open a PR; the `ci` check must pass before merging (squash only).
