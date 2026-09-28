# quiet-otter

- `main` is protected. Work on a branch and open a PR (`gh pr create --fill`); the `ci` check must pass.
- Never commit secrets, `.env` files, databases or `.DS_Store` (see `.gitignore`; CI rejects them).
- Before committing, run:

```sh
swift build && swift test
```
