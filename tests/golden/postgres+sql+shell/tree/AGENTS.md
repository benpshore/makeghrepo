# quiet-otter

- `main` is protected. Work on a branch and open a PR (`gh pr create --fill`); the `ci` check must pass.
- Never commit secrets, `.env` files, databases or `.DS_Store` (see `.gitignore`; CI rejects them).
- Before committing, run:

```sh
docker compose up -d db   # local Postgres; migrations in db/migrations run on first start
uvx sqlfluff lint sql db
shellcheck scripts/*.sh
```
