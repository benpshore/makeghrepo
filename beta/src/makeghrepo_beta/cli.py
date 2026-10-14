"""Beta CLI: all supported execution paths consume the same validated plan."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from makeghrepo_beta import package_version
from makeghrepo_beta.output import describe_report, width, wrap
from makeghrepo_beta.plan import LANGS, BootstrapPlan, Request, StatusPlan, build_plan, describe


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(
        prog="makeghrepo-beta",
        usage="%(prog)s [options]\n  [NAME/LANGUAGE ...]",
        allow_abbrev=False,
        formatter_class=lambda prog: argparse.HelpFormatter(
            prog, width=width(), max_help_position=12
        ),
        description=(
            "Experimental offline rendering and bootstrap inspection. "
            "Live bootstrap is unavailable. Dry-run never contacts GitHub."
        ),
        epilog=f"Languages (any number, or none): {', '.join(LANGS)}.",
    )
    cli.add_argument("words", nargs="*", metavar="NAME/LANGUAGE")
    cli.add_argument(
        "--render", type=Path, metavar="DIR", help="render offline into absent/empty DIR"
    )
    cli.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="validate and print the plan; no writes or subprocesses",
    )
    cli.add_argument(
        "--json",
        action="store_true",
        help="print JSON with --dry-run, --preflight or --status",
    )
    cli.add_argument(
        "--private", "-p", action="store_true", help="generate private-project metadata"
    )
    cli.add_argument(
        "--lib", action="store_true", help="python library layout without a console script"
    )
    cli.add_argument(
        "--license", choices=("none", "MIT"), default="none", help="project license (default: none)"
    )
    cli.add_argument(
        "--owner", help="owner metadata; preflight can resolve the authenticated account"
    )
    cli.add_argument("--root", type=Path, help="bootstrap directory root (no relocation)")
    cli.add_argument(
        "--preflight", action="store_true", help="read-only GitHub probes; no project writes"
    )
    cli.add_argument(
        "--status", type=Path, metavar="DIR", help="inspect recovery records; never replay or clean"
    )
    cli.add_argument("--author", default="Test User", help="offline author metadata")
    cli.add_argument("--description", default="a test project", help="offline project description")
    cli.add_argument("--year", default="2026", help="offline copyright year")
    cli.add_argument("--version", action="version", version=f"makeghrepo-beta {package_version()}")
    return cli


def main(argv: list[str] | None = None) -> None:
    cli = parser()
    args = cli.parse_intermixed_args(argv)
    if args.json and not (args.dry_run or args.preflight or args.status):
        cli.error("--json requires --dry-run, --preflight or --status")
    if args.dry_run and (args.preflight or args.status):
        cli.error("--dry-run cannot be combined with --preflight or --status")
    try:
        plan = build_plan(
            Request(
                words=tuple(args.words),
                render=args.render,
                private=args.private,
                lib=args.lib,
                license=args.license,
                owner=args.owner,
                author=args.author,
                description=args.description,
                year=args.year,
                root=args.root,
                status=args.status,
                preflight=args.preflight,
            )
        )
        if isinstance(plan, StatusPlan):
            from makeghrepo_beta.journal import inspect_records

            report = inspect_records(plan.destination)
            print(
                json.dumps(report, indent=2, sort_keys=True)
                if args.json
                else describe_report(report)
            )
            return
        if isinstance(plan, BootstrapPlan):
            if args.preflight:
                from makeghrepo_beta.preflight import inspect

                report = inspect(plan)
            elif args.dry_run:
                report = plan.as_dict()
            else:
                raise ValueError("live bootstrap is unavailable; use --dry-run or --preflight")
            print(
                json.dumps(report, indent=2, sort_keys=True)
                if args.json
                else describe_report(report)
            )
            return
        if args.dry_run:
            print(plan.as_json() if args.json else wrap(describe(plan)))
            return
        from makeghrepo_beta.execute import execute

        record = execute(plan)
    except (OSError, ValueError, RuntimeError) as exc:
        cli.exit(1, f"makeghrepo-beta: {exc}\n")
    except KeyboardInterrupt as exc:
        detail = str(exc) or (
            "interrupted during read-only inspection"
            if args.preflight or args.status
            else "interrupted before execution"
        )
        cli.exit(130, f"makeghrepo-beta: {detail}\n")
    print(f"rendered {plan.destination}")
    print(f"Recovery record:\n{record}")


if __name__ == "__main__":
    main()
