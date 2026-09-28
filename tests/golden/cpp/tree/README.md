# quiet-otter

quiet-otter

## Checks

Run these before opening a PR. CI runs the same ones.

```sh
cmake -S . -B build && cmake --build build && ctest --test-dir build
```

`main` is protected: open a PR; the `ci` check must pass before merging (squash only).
