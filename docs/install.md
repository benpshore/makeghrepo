# Install and update

Python with Copier and the native Rust binary are supported alternatives from the
same releases. Keep the Python route if you already use `uv tool install`; installing
Rust is optional. Both need Git and an authenticated GitHub CLI (`gh auth login`)
for repository creation. Offline rendering needs no GitHub authentication. Selected
language checks can additionally require their own toolchains.

## Python / Copier

Install the existing Git route with [uv](https://docs.astral.sh/uv/getting-started/installation/):

```sh
uv tool install --python 3.14 git+https://github.com/benpshore/makeghrepo
makeghrepo --version
makeghrepo --help
```

uv manages the required Python environment. If the command is not found, run
`uv tool update-shell`, then open a new terminal. To update that installation:

```sh
uv tool upgrade makeghrepo
makeghrepo --version
uv tool list
```

This assumes the original Git source was unpinned, as shown above. A source pinned
to a tag or commit keeps that constraint during an upgrade; use `uv tool install`
with the intended new source/ref to change it. See [uv's upgrade behavior](https://docs.astral.sh/uv/concepts/tools/#upgrading-tools).

## Native Rust binaries

Releases contain optimized binaries and `.sha256` files for these platforms:

| Platform | Asset suffix |
| --- | --- |
| Apple Silicon macOS | `aarch64-apple-darwin` |
| Linux x86_64 | `x86_64-unknown-linux-gnu` |
| Linux ARM64 | `aarch64-unknown-linux-gnu` |

The native Linux acceptance checks run on Ubuntu 24.04; the macOS checks run on an
Apple Silicon runner. Choose an asset matching your operating system and CPU.
No Rust compiler is needed to install it.

The following Apple Silicon example installs under **`makeghrepo-rs`**, keeping the
uv-managed `makeghrepo` executable intact. For Linux, change `target` to the matching
suffix in the table. Run the same sequence to update to a later release.

```sh
(
  set -eu
  target=aarch64-apple-darwin
  version=$(gh release view --repo benpshore/makeghrepo --json tagName --jq .tagName)
  download_dir=$(mktemp -d)
  trap 'rm -rf "$download_dir"' EXIT
  gh release download "$version" --repo benpshore/makeghrepo \
    --pattern "makeghrepo-$target" --pattern "makeghrepo-$target.sha256" \
    --dir "$download_dir"
  cd "$download_dir"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c "makeghrepo-$target.sha256"
  else
    shasum -a 256 -c "makeghrepo-$target.sha256"
  fi
  chmod 755 "makeghrepo-$target"
  binary_version=$("./makeghrepo-$target" --version)
  test "$binary_version" = "makeghrepo ${version#v}"
  mkdir -p "$HOME/.local/bin"
  install -m 755 "makeghrepo-$target" "$HOME/.local/bin/makeghrepo-rs"
  "$HOME/.local/bin/makeghrepo-rs" --version
)
```

Invoke `~/.local/bin/makeghrepo-rs`, or add `~/.local/bin` to your shell's `PATH`.
An unavailable asset or failed checksum stops the sequence before installation.

## Check the installed tool

These commands render into new local directories without creating a GitHub repo,
running generated-project checks, or publishing anything:

```sh
makeghrepo preview python --lib --private --render ./preview-python
makeghrepo-rs preview rust --render ./preview-rust
```

New projects have no license file or license metadata by default. Add `--license MIT`
only when that is the intended license. Both tools refuse to overwrite a nonempty
render directory. For repository creation, omit `--render`; its normal checks and
GitHub configuration then apply. Installing an update does not change any existing
repository's live rules.

## Release verification

CI runs `scripts/distribution-check` on all three native architectures. It builds
an optimized Rust binary and a wheel, installs the wheel into a clean uv tool
directory, and upgrades a separate Git-installed tool from v0.33.0 to the exact
candidate commit. Candidate version v0.34.0 is a disposable local fixture; no
release or remote tag is created. Installed tools must pass help/version checks
and render matching file names, bytes, and executable bits for eight representative
public/private and licensed/unlicensed projects.

The installed wheel and Rust binary also run five real-Git bootstrap and retry
scenarios against a controlled GitHub fixture, comparing their resulting settings,
rulesets and push behavior. These tests use local bare remotes and never call
GitHub or change live policy.

The workflow's optional `release` input runs the same installation/render checks
against a published stable tag instead, verifying the native asset's checksum
before execution. Each runner uploads JSON evidence containing the tested version,
commit transition where applicable, artifact hashes, rendered-tree hashes,
bootstrap configuration results, and elapsed stage times. These checks complement the full generated-project CI matrix;
they do not exercise live GitHub administrative settings.
