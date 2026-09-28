# quiet-otter

quiet-otter

## Checks

Run these before opening a PR. CI runs the same ones.

```sh
cargo fmt && cargo clippy --all-targets -- -D warnings && cargo test
docker build -t quiet-otter .
```

`main` is protected: open a PR; the `ci` check must pass before merging (squash only).
