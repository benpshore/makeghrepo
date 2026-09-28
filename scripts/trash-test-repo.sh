#!/usr/bin/env bash
# Delete a throwaway test repo that makeghrepo created: its GitHub Project board(s),
# the GitHub repo, and the local clone. Every guard runs before anything is deleted.
#
#   scripts/trash-test-repo.sh [--dry-run] <name>
#
# <name> is the bare repo name and must be mgr-test-* or *-trash. Refused unless the
# repo belongs to the authenticated gh user and was created less than 24 hours ago,
# and the local clone ${MAKEGHREPO_DIR:-$HOME/code/GitHub}/<name>, if present, is a
# real directory whose origin is that repo. TRASH_NOW_EPOCH overrides "now" (tests).
set -euo pipefail

usage() {
  echo "usage: $0 [--dry-run] <name>   (name: mgr-test-* or *-trash)" >&2
  exit 2
}

die() {
  echo "refusing: $*" >&2
  exit 1
}

dry_run=0
name=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=1 ;;
    -*) usage ;;
    *)
      [[ -z "$name" ]] || usage
      name="$arg"
      ;;
  esac
done
[[ -n "$name" ]] || usage

# Guard 1: throwaway names only, fully anchored (no slashes, dots or uppercase).
if [[ ! "$name" =~ ^mgr-test-[a-z0-9][a-z0-9-]*$ && ! "$name" =~ ^[a-z0-9][a-z0-9-]*-trash$ ]]; then
  die "'$name' is not a throwaway name (mgr-test-* or *-trash)"
fi

# Guard 2: the repo exists and belongs to the authenticated user.
owner=$(gh api user --jq .login)
[[ "$owner" =~ ^[A-Za-z0-9-]+$ ]] || die "unexpected gh user '$owner'"
info=$(gh api "repos/$owner/$name" --jq '[.owner.login, .created_at] | @tsv') ||
  die "$owner/$name not found on GitHub"
repo_owner=${info%%$'\t'*}
created_at=${info#*$'\t'}
[[ "$repo_owner" == "$owner" ]] || die "$owner/$name belongs to '$repo_owner', not '$owner'"

# Guard 3: created less than 24 hours ago.
now=${TRASH_NOW_EPOCH:-$(date -u +%s)}
created=$(date -u -d "$created_at" +%s) || die "can't parse created_at '$created_at'"
((now - created < 86400)) || die "$owner/$name was created $created_at, over 24 hours ago"

# Guard 4: if the local clone exists, it must really be this repo's clone.
base_dir="${MAKEGHREPO_DIR:-$HOME/code/GitHub}"
dir="$base_dir/$name"
remove_dir=0
if [[ -e "$dir" || -L "$dir" ]]; then
  [[ ! -L "$dir" ]] || die "$dir is a symlink"
  [[ -d "$dir" ]] || die "$dir is not a directory"
  [[ "$(readlink -f -- "$dir")" == "$(readlink -f -- "$base_dir")/$name" ]] ||
    die "$dir resolves outside $base_dir"
  origin=$(git -C "$dir" remote get-url origin 2>/dev/null) || die "$dir has no git origin"
  case "$origin" in
    "https://github.com/$owner/$name" | "https://github.com/$owner/$name.git" | \
      "git@github.com:$owner/$name" | "git@github.com:$owner/$name.git") ;;
    *) die "$dir has origin '$origin', not github.com/$owner/$name" ;;
  esac
  remove_dir=1
fi

# A plain assignment, so a failing gh aborts under set -e instead of silently yielding no boards.
board_list=$(gh project list --owner "$owner" --format json --limit 1000 \
  --jq ".projects[] | select(.title == \"$name\") | .number")
boards=()
while IFS= read -r number; do
  [[ -n "$number" ]] || continue
  [[ "$number" =~ ^[0-9]+$ ]] || die "unexpected project number '$number'"
  boards+=("$number")
done <<<"$board_list"

act() {
  local what="$1"
  shift
  if ((dry_run)); then
    echo "- would $what"
  else
    echo "- $what"
    "$@"
  fi
}

for number in "${boards[@]}"; do
  act "delete project board #$number ($name)" gh project delete "$number" --owner "$owner"
done
act "delete repo $owner/$name" gh repo delete "$owner/$name" --yes
if ((remove_dir)); then
  act "remove $dir" rm -rf -- "$dir"
fi
