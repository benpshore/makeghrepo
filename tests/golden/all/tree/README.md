# quiet-otter

quiet-otter

## Checks

Run these before opening a PR. CI runs the same ones.

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

`main` is protected: open a PR; the `ci` check must pass before merging (squash only).
