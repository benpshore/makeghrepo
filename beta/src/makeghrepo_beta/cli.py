"""Beta CLI: all supported execution paths consume the same validated plan."""

from __future__ import annotations

import argparse
from pathlib import Path

from makeghrepo_beta import package_version
from makeghrepo_beta.plan import LANGS, Request, build_plan, describe


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(
        prog="makeghrepo-beta",
        description=(
            "Experimental offline generation. Requires --render DIR. "
            "GitHub creation remains in stable makeghrepo until beta preflight/recovery is ready."
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
        help="print the dry-run plan as JSON (requires --dry-run)",
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
        "--owner", default="someone", help="offline owner metadata (no authentication)"
    )
    cli.add_argument("--author", default="Test User", help="offline author metadata")
    cli.add_argument("--description", default="a test project", help="offline project description")
    cli.add_argument("--year", default="2026", help="offline copyright year")
    cli.add_argument("--version", action="version", version=f"makeghrepo-beta {package_version()}")
    return cli


def main(argv: list[str] | None = None) -> None:
    cli = parser()
    args = cli.parse_args(argv)
    if args.json and not args.dry_run:
        cli.error("--json requires --dry-run")
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
            )
        )
        if args.dry_run:
            print(plan.as_json() if args.json else describe(plan))
            return
        from makeghrepo_beta.execute import execute

        execute(plan)
    except (OSError, ValueError, RuntimeError) as exc:
        cli.exit(1, f"makeghrepo-beta: {exc}\n")
    except KeyboardInterrupt:
        cli.exit(130, "makeghrepo-beta: interrupted; inspect any reported retained paths\n")
    print(f"rendered {plan.destination}")


if __name__ == "__main__":
    main()
