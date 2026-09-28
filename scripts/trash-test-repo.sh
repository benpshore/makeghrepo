#!/usr/bin/env bash
#
# trash-test-repo.sh: safely delete a throwaway repo created for live testing.
#
# Usage: scripts/trash-test-repo.sh <repo-name>
#
# Guardrails (S0d):
# - The name must start with "mgr-test-" or end with "-trash", and may only use
#   GitHub's repo-name charset [A-Za-z0-9._-] (so it is always one path segment).
# - The repo must exist on GitHub and be at most 24 hours old.
#
# It then deletes, in this order:
#   1. every GitHub Project board whose title is exactly <repo-name>
#   2. the local clone at ${MAKEGHREPO_DIR:-~/code/GitHub}/<repo-name>
#   3. the GitHub repo itself
# The repo goes last on purpose: it is the only trustworthy age anchor, so if an
# earlier step fails the script stops with the repo intact and can be re-run.
#
# Agents never call "gh repo delete" directly; they use this script instead.

set -euo pipefail

OWNER="benpshore"
MAXAGE_HOURS=24
PROJECT_LIMIT=1000

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <repo-name>" >&2
    echo "" >&2
    echo "Safely delete a throwaway test repository. Only repos matching" >&2
    echo "^mgr-test- or ending with -trash, created within $MAXAGE_HOURS hours, can be deleted." >&2
    exit 1
fi

REPO_NAME="$1"

if ! [[ "$REPO_NAME" =~ ^(mgr-test-[A-Za-z0-9._-]+|[A-Za-z0-9._-]+-trash)$ ]]; then
    echo "Error: repo name must match '^mgr-test-' or end with '-trash'" >&2
    echo "  (and use only the characters A-Z a-z 0-9 . _ -)" >&2
    echo "  Got: $REPO_NAME" >&2
    exit 1
fi

# --- Age guard -----------------------------------------------------------------

echo "Fetching repo info for $OWNER/$REPO_NAME..."
if ! REPO_INFO=$(gh repo view "$OWNER/$REPO_NAME" --json createdAt); then
    echo "Error: repo $OWNER/$REPO_NAME not found or inaccessible; nothing was deleted" >&2
    exit 1
fi

# Timezone-aware on both sides: the host clock need not be UTC.
if ! AGE_SECONDS=$(python3 -c '
import json, sys
from datetime import datetime, timezone
created = json.load(sys.stdin)["createdAt"]
created_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
print(int((datetime.now(timezone.utc) - created_at).total_seconds()))
' <<< "$REPO_INFO"); then
    echo "Error: failed to parse repo creation time; nothing was deleted" >&2
    exit 1
fi

if (( AGE_SECONDS > MAXAGE_HOURS * 3600 )); then
    echo "Error: repo is $((AGE_SECONDS / 3600))h$(((AGE_SECONDS % 3600) / 60))m old," \
        "over the $MAXAGE_HOURS-hour safety limit; nothing was deleted" >&2
    exit 1
fi
echo "Repo is $((AGE_SECONDS / 3600))h$(((AGE_SECONDS % 3600) / 60))m old (limit $MAXAGE_HOURS hours)"

# --- 1. Project board(s) ---------------------------------------------------------

# makeghrepo names the board exactly after the repo (github.py configure_project).
# --closed lists every board, so totalCount must equal the number returned.
echo "Looking for project boards titled $REPO_NAME..."
if ! PROJECTS_JSON=$(gh project list --owner "$OWNER" --closed --limit "$PROJECT_LIMIT" --format json); then
    echo "Error: could not list project boards for $OWNER; nothing was deleted" >&2
    exit 1
fi

if ! BOARD_NUMBERS=$(NAME="$REPO_NAME" python3 -c '
import json, os, sys
data = json.load(sys.stdin)
projects, total = data["projects"], data["totalCount"]
if total > len(projects):
    sys.exit(f"project list truncated: got {len(projects)} of {total}")
for p in projects:
    if p["title"] == os.environ["NAME"]:
        print(p["number"])
' <<< "$PROJECTS_JSON"); then
    echo "Error: could not read the project board list; nothing was deleted" >&2
    exit 1
fi

if [[ -z "$BOARD_NUMBERS" ]]; then
    echo "  No project board found"
fi
for NUMBER in $BOARD_NUMBERS; do
    echo "Deleting project board #$NUMBER..."
    if ! gh project delete "$NUMBER" --owner "$OWNER" > /dev/null; then
        echo "Error: failed to delete project board #$NUMBER; the repo was not deleted" >&2
        exit 1
    fi
    echo "  Project board #$NUMBER deleted"
done

# --- 2. Local clone ----------------------------------------------------------------

BASE_DIR="${MAKEGHREPO_DIR:-$HOME/code/GitHub}"
BASE_DIR="${BASE_DIR/#\~/$HOME}" # like Path.expanduser() in cli.py
LOCAL_DIR="$BASE_DIR/$REPO_NAME"
if [[ -e "$LOCAL_DIR" || -L "$LOCAL_DIR" ]]; then
    echo "Deleting local clone at $LOCAL_DIR..."
    if ! rm -rf -- "$LOCAL_DIR"; then
        echo "Error: failed to delete $LOCAL_DIR; the repo was not deleted" >&2
        exit 1
    fi
    echo "  Local clone deleted"
else
    echo "  No local clone at $LOCAL_DIR"
fi

# --- 3. Repo -----------------------------------------------------------------------

echo "Deleting repository $OWNER/$REPO_NAME..."
if ! gh repo delete "$OWNER/$REPO_NAME" --yes; then
    echo "Error: failed to delete repository $OWNER/$REPO_NAME" >&2
    exit 1
fi
echo "  Repository deleted"

echo ""
echo "Successfully deleted $REPO_NAME"
