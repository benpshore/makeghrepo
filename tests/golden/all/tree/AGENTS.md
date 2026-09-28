# quiet-otter

- `main` is protected. Work on a branch and open a PR (`gh pr create --fill`); the `ci` check must pass.
- Never commit secrets, `.env` files, databases or `.DS_Store` (see `.gitignore`; CI rejects them).
- Python: use **uv** only (`uv add`, `uv run`). Never pip.
- Before committing, run:

```sh
uv run ruff format && uv run ruff check && uv run pytest && uv audit --preview-features audit-command
cargo fmt && cargo clippy --all-targets -- -D warnings && cargo test
swift build && swift test
npm run lint && npm test
npm run lint:css
cmake -S . -B build && cmake --build build && ctest --test-dir build
npx @stoplight/spectral-cli lint openapi.yaml
docker compose up -d db   # local Postgres; migrations in db/migrations run on first start
uvx sqlfluff lint sql db
docker build -t quiet-otter .
shellcheck scripts/*.sh
```
