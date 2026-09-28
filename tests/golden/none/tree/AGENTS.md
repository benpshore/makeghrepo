# quiet-otter

- `main` is protected. Work on a branch and open a PR (`gh pr create --fill`); the `ci` check must pass.
- Never commit secrets, `.env` files, databases or `.DS_Store` (see `.gitignore`; CI rejects them).
- Before committing, run:

```sh
# no language chosen yet: add your toolchain's checks here and to .github/workflows/ci.yml
```
