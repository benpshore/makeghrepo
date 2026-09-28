# quiet-otter

- `main` is protected. Work on a branch and open a PR (`gh pr create --fill`); the `ci` check must pass.
- Never commit secrets, `.env` files, databases or `.DS_Store` (see `.gitignore`; CI rejects them).
- Python: use **uv** only (`uv add`, `uv run`). Never pip.
- Before committing, run:

```sh
uv run ruff format && uv run ruff check && uv run pytest && uv audit --preview-features audit-command
swift build && swift test
```
