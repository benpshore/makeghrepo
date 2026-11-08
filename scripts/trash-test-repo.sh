#!/bin/sh
# Delete a throwaway test repo that makeghrepo created: its GitHub Project board(s),
# the GitHub repo, and the local clone. Every guard runs before anything is deleted.
#
#   scripts/trash-test-repo.sh [--dry-run] <name>
#
# <name> is the bare repo name and must be mgr-test-* or *-trash. Refused unless the
# repo belongs to the authenticated gh user and was created less than 24 hours ago,
# and the local clone ${MAKEGHREPO_DIR:-$HOME/code/GitHub}/<name>, if present, is a
# real directory whose origin is that repo. TRASH_NOW_EPOCH overrides "now" (tests).
set -eu
# Bracket ranges in case patterns below must mean ASCII, whatever the user's locale.
LC_ALL=C
export LC_ALL

usage() {
  echo "usage: $0 [--dry-run] <name>   (name: mgr-test-* or *-trash)" >&2
  exit 2
}

die() {
  echo "refusing: $*" >&2
  exit 1
}

# Run gh with stdout untouched and its exit status preserved. gh's stderr is
# passed on without its suggestion to re-authenticate, which would replace the
# user's stored credential.
gh_run() {
  exec 3>&1
  gh_status=0
  gh_err=$(gh "$@" 2>&1 >&3 3>&-) || gh_status=$?
  exec 3>&-
  if [ -n "${gh_err}" ]; then
    printf '%s\n' "${gh_err}" |
      sed -e 's/[[:space:]]*To request it, run:.*$//' -e '/gh[[:space:]][[:space:]]*auth[[:space:]]/d' >&2
  fi
  return "${gh_status}"
}

dry_run=0
name=""
for arg in "$@"; do
  case "${arg}" in
    --dry-run) dry_run=1 ;;
    -*) usage ;;
    *)
      [ -z "${name}" ] || usage
      name="${arg}"
      ;;
  esac
done
[ -n "${name}" ] || usage

# Guard 1: throwaway names only, fully anchored (no slashes, dots or uppercase).
case "${name}" in
  *[!a-z0-9-]*) die "'${name}' is not a throwaway name (mgr-test-* or *-trash)" ;;
  mgr-test-[a-z0-9]* | [a-z0-9]*-trash) ;;
  *) die "'${name}' is not a throwaway name (mgr-test-* or *-trash)" ;;
esac

# Guard 2: the repo exists and belongs to the authenticated user.
owner=$(gh_run api user --jq .login)
case "${owner}" in
  "" | *[!A-Za-z0-9-]*) die "unexpected gh user '${owner}'" ;;
  *) ;;
esac
set +e
info=$(gh_run api "repos/${owner}/${name}" --jq '[.owner.login, .created_at] | @tsv')
info_status=$?
set -e
[ "${info_status}" -eq 0 ] || die "${owner}/${name} not found on GitHub"
tab=$(printf '\t')
repo_owner=${info%%"${tab}"*}
created_at=${info#*"${tab}"}
[ "${repo_owner}" = "${owner}" ] || die "${owner}/${name} belongs to '${repo_owner}', not '${owner}'"

# Guard 3: created less than 24 hours ago.
now=${TRASH_NOW_EPOCH:-$(date -u +%s)}
case "${now}" in
  "" | *[!0-9]*) die "can't read the current time '${now}'" ;;
  *) ;;
esac
# GitHub returns UTC ISO-8601. Check the exact shape first: BSD date ignores trailing junk.
case "${created_at}" in
  [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z) ;;
  *) die "can't parse created_at '${created_at}'" ;;
esac
# GNU date (Linux), then BSD date (macOS).
created=$(date -u -d "${created_at}" +%s 2>/dev/null ||
  date -j -u -f '%Y-%m-%dT%H:%M:%SZ' "${created_at}" +%s 2>/dev/null) ||
  die "can't parse created_at '${created_at}'"
case "${created}" in
  "" | *[!0-9]*) die "can't parse created_at '${created_at}'" ;;
  *) ;;
esac
[ $((now - created)) -lt 86400 ] || die "${owner}/${name} was created ${created_at}, over 24 hours ago"

# Guard 4: if the local clone exists, it must really be this repo's clone.
base_dir="${MAKEGHREPO_DIR:-${HOME}/code/GitHub}"
dir="${base_dir}/${name}"
remove_dir=0
if [ -e "${dir}" ] || [ -L "${dir}" ]; then
  [ ! -L "${dir}" ] || die "${dir} is a symlink"
  [ -d "${dir}" ] || die "${dir} is not a directory"
  real_dir=$(cd -P -- "${dir}" && pwd -P) || die "can't resolve ${dir}"
  real_base=$(cd -P -- "${base_dir}" && pwd -P) || die "can't resolve ${base_dir}"
  [ "${real_dir}" = "${real_base}/${name}" ] || die "${dir} resolves outside ${base_dir}"
  origin=$(git -C "${dir}" remote get-url origin 2>/dev/null) || die "${dir} has no git origin"
  case "${origin}" in
    "https://github.com/${owner}/${name}" | "https://github.com/${owner}/${name}.git" | \
      "git@github.com:${owner}/${name}" | "git@github.com:${owner}/${name}.git") ;;
    *) die "${dir} has origin '${origin}', not github.com/${owner}/${name}" ;;
  esac
  remove_dir=1
fi

# A plain assignment, so a failing gh aborts under set -e instead of silently yielding no boards.
board_list=$(gh_run project list --owner "${owner}" --format json --limit 1000 \
  --jq ".projects[] | select(.title == \"${name}\") | .number")
boards=""
while IFS= read -r number; do
  [ -n "${number}" ] || continue
  case "${number}" in
    *[!0-9]*) die "unexpected project number '${number}'" ;;
    *) ;;
  esac
  boards="${boards} ${number}"
done <<EOF
${board_list}
EOF

act() {
  what=$1
  shift
  if [ "${dry_run}" -eq 1 ]; then
    echo "- would ${what}"
  else
    echo "- ${what}"
    "$@"
  fi
}

# Every entry was checked to be digits only, so word splitting is exact here.
for number in ${boards}; do
  act "delete project board #${number} (${name})" gh_run project delete "${number}" --owner "${owner}"
done
act "delete repo ${owner}/${name}" gh_run repo delete "${owner}/${name}" --yes
if [ "${remove_dir}" -eq 1 ]; then
  act "remove ${dir}" rm -rf -- "${dir}"
fi
