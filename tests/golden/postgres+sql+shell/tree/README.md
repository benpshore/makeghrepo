# quiet-otter

quiet-otter

## Checks

Run these before opening a PR. CI runs the same ones.

```sh
docker compose up -d db   # local Postgres; migrations in db/migrations run on first start
uvx sqlfluff lint sql db
shellcheck scripts/*.sh
```

`main` is protected: open a PR; the `ci` check must pass before merging (squash only).
