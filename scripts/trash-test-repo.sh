#!/usr/bin/env bash
#
# trash-test-repo.sh: safely delete throwaway test repositories created by makeghrepo agents.
#
# Usage: ./scripts/trash-test-repo.sh <repo-name>
#
# This script enforces safety guardrails (S0d):
# - Only deletes repos whose names match ^mgr-test- or end with -trash$
# - Only deletes repos created within the last 24 hours
# - Deletes the GitHub Project board, repository, and local clone
#
# Agents never call "gh repo delete" directly; they use this script instead.

set -euo pipefail

# Configuration
OWNER="benpshore"
MAXAGE_HOURS=24

# Parse arguments
if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <repo-name>" >&2
    echo "" >&2
    echo "Safely delete a throwaway test repository. Only repos matching" >&2
    echo "^mgr-test- or ending with -trash$ created within 24 hours can be deleted." >&2
    exit 1
fi

REPO_NAME="$1"

# Safety check: validate repo name
if ! [[ "$REPO_NAME" =~ ^mgr-test- ]] && ! [[ "$REPO_NAME" =~ -trash$ ]]; then
    echo "Error: repo name must match '^mgr-test-' or end with '-trash'" >&2
    echo "  Got: $REPO_NAME" >&2
    exit 1
fi

# Fetch repo info from GitHub
echo "Fetching repo info for $OWNER/$REPO_NAME..."
REPO_INFO=$(gh repo view "$OWNER/$REPO_NAME" --json createdAt 2>/dev/null) || {
    echo "Error: repo $OWNER/$REPO_NAME not found or inaccessible" >&2
    exit 1
}

# Extract creation timestamp using Python (jq may not be available)
CREATED_AT=$(python3 -c "import json, sys; print(json.load(sys.stdin)['createdAt'])" <<< "$REPO_INFO") || {
    echo "Error: failed to parse repo creation time" >&2
    exit 1
}

# Calculate repo age in hours
CREATED_EPOCH=$(date -d "$CREATED_AT" +%s)
NOW_EPOCH=$(date +%s)
AGE_SECONDS=$((NOW_EPOCH - CREATED_EPOCH))
AGE_HOURS=$((AGE_SECONDS / 3600))

# Safety check: repo must be less than 24 hours old
if [[ $AGE_HOURS -gt $MAXAGE_HOURS ]]; then
    echo "Error: repo is $AGE_HOURS hours old, exceeds $MAXAGE_HOURS-hour safety limit" >&2
    echo "  Created: $CREATED_AT" >&2
    exit 1
fi

echo "Repo is $AGE_HOURS hours old (within limit of $MAXAGE_HOURS hours)"

# Attempt to delete associated GitHub Project board
# The project board may not exist, so we don't fail if it doesn't
echo "Checking for associated GitHub Project..."
# Use Python to find a project board matching the repo name
PROJECT_ID=$(gh project list --owner "$OWNER" --json id,title 2>/dev/null | python3 -c "
import json, sys
try:
    projects = json.load(sys.stdin)
    for p in projects:
        if '$REPO_NAME' in p.get('title', ''):
            print(p['id'])
            break
except:
    pass
" || true)

if [[ -n "${PROJECT_ID:-}" ]]; then
    echo "Deleting GitHub Project $PROJECT_ID..."
    # Note: gh project delete typically requires --confirm or interactive confirmation
    # We use --confirm to make this script non-interactive for agents
    if gh project delete "$PROJECT_ID" --owner "$OWNER" --confirm 2>/dev/null; then
        echo "  Project deleted"
    else
        echo "  Warning: could not delete project (may have already been deleted)"
    fi
else
    echo "  No project board found"
fi

# Delete the GitHub repository
echo "Deleting repository $OWNER/$REPO_NAME..."
if gh repo delete "$OWNER/$REPO_NAME" --yes; then
    echo "  Repository deleted"
else
    echo "Error: failed to delete repository" >&2
    exit 1
fi

# Delete the local clone if it exists
LOCAL_DIR="/home/pi5coreadmin/code/GitHub/$REPO_NAME"
if [[ -d "$LOCAL_DIR" ]]; then
    echo "Deleting local clone at $LOCAL_DIR..."
    rm -rf "$LOCAL_DIR"
    echo "  Local clone deleted"
else
    echo "  No local clone found at $LOCAL_DIR"
fi

echo ""
echo "Successfully deleted $REPO_NAME"
