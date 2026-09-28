# quiet-otter

quiet-otter

## Checks

Run these before opening a PR. CI runs the same ones.

```sh
uvx sqlfluff lint sql
```

`main` is protected: open a PR; the `ci` check must pass before merging (squash only).
