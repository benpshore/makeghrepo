# quiet-otter

quiet-otter

## Checks

Run these before opening a PR. CI runs the same ones.

```sh
npm run lint && npm test
npm run lint:css
npx @stoplight/spectral-cli lint openapi.yaml
```

`main` is protected: open a PR; the `ci` check must pass before merging (squash only).
