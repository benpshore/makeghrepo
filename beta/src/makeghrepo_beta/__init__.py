"""The beta distribution owns only the makeghrepo-beta executable."""

from importlib.metadata import PackageNotFoundError, version


def package_version() -> str:
    try:
        return version("makeghrepo-beta")
    except PackageNotFoundError:
        return "uninstalled"


def main() -> None:
    from makeghrepo_beta.cli import main as run

    run()
