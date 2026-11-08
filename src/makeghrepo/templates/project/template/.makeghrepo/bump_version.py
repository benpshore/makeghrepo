"""Create one local patch tag for a clean commit; never push or publish."""

import argparse
import re
import shutil
import subprocess
import sys

STABLE = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def git(*args: str) -> str:
    executable = shutil.which("git")
    if executable is None:
        raise ValueError("Git is required for version tags")
    return subprocess.check_output(  # noqa: S603 -- fixed Git executable and structured arguments
        [executable, *args], text=True, stderr=subprocess.PIPE
    ).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print the next tag; change nothing")
    args = parser.parse_args()
    try:
        head = git("rev-parse", "HEAD")
        existing = [t for t in git("tag", "--points-at", head).splitlines() if STABLE.fullmatch(t)]
        if len(existing) > 1:
            raise ValueError("HEAD has multiple stable tags; resolve them manually")
        if existing:
            print(f"Already tagged {existing[0]}; no changes")
            return 0
        versions = [
            tuple(map(int, match.groups()))
            for tag in git("tag", "--merged", head).splitlines()
            if (match := STABLE.fullmatch(tag))
        ]
        major, minor, patch = max(versions, default=(0, 0, 0))
        tag = f"v{major}.{minor}.{patch + 1}"
        # An existing tag on an unrelated branch must never be moved or reused.
        if tag in git("tag", "--list").splitlines():
            raise ValueError(f"{tag} already exists on another commit")
        if args.dry_run:
            print(f"Would create local tag {tag} at {head}; nothing pushed or published")
            return 0
        if git("status", "--porcelain"):
            raise ValueError(
                "working tree must be clean; commit through the normal PR workflow first"
            )
        if git("rev-parse", "HEAD") != head:
            raise ValueError("HEAD changed while planning; run the command again")
        git("tag", "-a", tag, "-m", tag, head)
        print(f"Created local tag {tag}; nothing pushed or published")
        return 0
    except (subprocess.CalledProcessError, ValueError) as error:
        print(f"bump:patch: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
