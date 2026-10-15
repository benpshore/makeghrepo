"""Explicit beta cloud plan/consume commands; never enter stable bootstrap."""

from __future__ import annotations

import argparse
from pathlib import Path

from makeghrepo_beta.cloud_contract import CloudPlan, canonical


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="makeghrepo-beta cloud", allow_abbrev=False)
    parser.add_argument("operation", choices=("plan", "fetch"))
    parser.add_argument("--source", required=True, help="reviewed makeghrepo commit (full SHA)")
    parser.add_argument("--owner", required=True, help="daughter repository owner (configuration)")
    parser.add_argument("--name", required=True, help="daughter repository name (configuration)")
    parser.add_argument("--run", type=int, help="existing completed trusted CI run ID")
    parser.add_argument("--destination", type=Path, help="local directory for verified result")
    parser.add_argument("--dry-run", action="store_true", help="validate and print only; no gh calls")
    args = parser.parse_args(argv)
    if args.operation == "fetch" and (args.run is None or args.destination is None):
        parser.error("fetch requires --run and --destination")
    if args.operation == "plan" and (args.run is not None or args.destination is not None):
        parser.error("--run and --destination apply only to fetch")
    try:
        plan = CloudPlan(args.source, args.owner, args.name)
        if args.operation == "plan" or args.dry_run:
            print(canonical(plan.document()).decode(), end="")
            return
        from makeghrepo_beta.cloud_client import consume

        outcome = consume(plan, args.run, args.destination)
        print(f"{outcome}: {args.destination.absolute()}")
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(1, f"makeghrepo-beta cloud: {exc}\n")
    except KeyboardInterrupt as exc:
        parser.exit(130, f"makeghrepo-beta cloud: {exc or 'interrupted; retry the same run'}\n")
