"""The repository is identified by its immutable numeric ID, never by a slug pin.

After the transfer to Shorewood-Analytics the old slug ``benpshore/makeghrepo``
may remain only in historical records. Anything that routes credentials,
guards a workflow, or binds provenance derives the slug at run time and checks
it against ``REPOSITORY_ID``.
"""

import json
import re
import runpy
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
OLD_SLUG = "benpshore/makeghrepo"
REPOSITORY_ID = 1386536822
# Historical records keep the slug as it was at the time; nothing reads them for routing.
HISTORICAL = {
    "docs/cloud-assets.md",
    "docs/one-open-pr-rollout.md",
    "docs/reconciliation-heads.json",
    "docs/roadmap.md",
    # Asserts that the pre-transfer policy schema is refused.
    ".github/one-open-pr/reconcile.test.mjs",
    __file__[len(str(ROOT)) + 1 :],
}


def tracked_files():
    try:
        output = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
        ).stdout
        return [ROOT / name for name in output.decode().split("\0") if name]
    except OSError, subprocess.CalledProcessError:
        skip = {".git", ".venv", "out", "node_modules", "__pycache__"}
        return [
            path
            for path in ROOT.rglob("*")
            if path.is_file() and not (set(path.relative_to(ROOT).parts) & skip)
        ]


def test_old_slug_survives_only_in_historical_records():
    offenders = {}
    for path in tracked_files():
        try:
            text = path.read_text("utf-8")
        except UnicodeDecodeError, OSError:
            continue
        if OLD_SLUG in text:
            offenders[str(path.relative_to(ROOT))] = text.count(OLD_SLUG)
    assert set(offenders) <= HISTORICAL, {k: v for k, v in offenders.items() if k not in HISTORICAL}


def test_every_identity_site_agrees_on_the_repository_id():
    policy = json.loads((ROOT / ".github/one-open-pr/policy.json").read_text())
    assert policy["repository_id"] == REPOSITORY_ID and "repository" not in policy
    reconcile = (ROOT / ".github/one-open-pr/reconcile.mjs").read_text()
    assert f"export const REPOSITORY_ID = {REPOSITORY_ID};" in reconcile
    assert "export const REPOSITORY =" not in reconcile
    workflow = (ROOT / ".github/workflows/one-open-pr.yml").read_text()
    assert f"github.repository_id == '{REPOSITORY_ID}'" in workflow
    assert not re.search(r"github\.repository ==", workflow)
    contract = (ROOT / "beta/src/makeghrepo_beta/cloud_contract.py").read_text()
    assert f"REPOSITORY_ID = {REPOSITORY_ID}" in contract
    check = runpy.run_path(str(ROOT / "scripts/distribution-check"))
    assert check["REPOSITORY_ID"] == REPOSITORY_ID


def test_merge_queue_coverage_keeps_write_tokens_out():
    for name in ("ci.yml", "codeql.yml"):
        text = (ROOT / ".github/workflows" / name).read_text()
        assert re.search(r"^  merge_group:\s*$", text, re.M), name
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    # Every permission grant in ci.yml is read-only; publication lives elsewhere.
    assert not re.search(r"^\s+(contents|packages|id-token|pull-requests): write", ci, re.M)
    for name in ("auto-release.yml", "release.yml", "release-build.yml"):
        assert "merge_group" not in (ROOT / ".github/workflows" / name).read_text(), name


def test_canonical_links_point_at_the_organization():
    for name in ("pyproject.toml", "README.md", "docs/install.md"):
        assert "Shorewood-Analytics/makeghrepo" in (ROOT / name).read_text(), name
