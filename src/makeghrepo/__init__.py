"""Bootstrap a new uv project from a template and publish it to GitHub."""


def main() -> None:
    from makeghrepo.cli import app

    app()
