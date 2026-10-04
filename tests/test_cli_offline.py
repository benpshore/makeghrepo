import os
import subprocess
import sys
import tomllib

import golden_snapshots
import pytest
from rich.text import Text
from typer.testing import CliRunner

from makeghrepo import cli, github, gitops, scaffold

runner = CliRunner()


@pytest.mark.parametrize("license_choice", [None, "none", "MIT"])
def test_offline_render_preserves_explicit_license_choice(tmp_path, license_choice):
    dest = tmp_path / "output"
    args = ["sample", "python", "--private", "--render", str(dest)]
    if license_choice is not None:
        args += ["--license", license_choice]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    metadata = tomllib.loads((dest / "pyproject.toml").read_text())["project"]
    assert (dest / "LICENSE").exists() is (license_choice == "MIT")
    assert metadata.get("license") == ("MIT" if license_choice == "MIT" else None)
    assert not (dest / ".git").exists()


@pytest.fixture(autouse=True)
def no_bootstrap_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline command entered the bootstrap workflow")

    for module, functions in (
        (github, ("current_user", "repo_exists", "create_repo", "configure_all")),
        (
            gitops,
            ("author_from_git_config", "init", "ensure_identity", "write_marker", "push_main"),
        ),
        (scaffold, ("smoke_test",)),
    ):
        for name in functions:
            monkeypatch.setattr(module, name, forbidden)


@pytest.mark.parametrize("option", ["-h", "--help"])
def test_help_lists_supported_languages_without_auth(option):
    result = runner.invoke(cli.app, [option])
    assert result.exit_code == 0, result.output
    help_text = Text.from_ansi(result.output).plain
    assert "--render" in help_text
    for language in scaffold.LANGUAGES:
        assert language in help_text


def test_version_exits_before_validating_workflow_arguments(monkeypatch):
    monkeypatch.setattr(cli, "_version", lambda: "1.2.3")
    result = runner.invoke(cli.app, ["invalid", "cobol", "--version"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "makeghrepo 1.2.3"


@pytest.mark.parametrize("combo", ["base", "python-lib", "all-private"])
def test_offline_cli_matches_shared_golden_output(tmp_path, combo):
    dest = tmp_path / "output"
    data = golden_snapshots.COMBOS[combo]
    args = [*data["languages"], "--render", str(dest)]
    if data.get("private"):
        args.append("--private")
    if data.get("py_lib"):
        args.append("--lib")
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert golden_snapshots.serialize(dest) == golden_snapshots.golden_path(combo).read_text()
    assert not (dest / ".git").exists()


def test_render_normalizes_name_and_uses_fixed_metadata(tmp_path):
    dest = tmp_path / "output"
    description = 'A "quoted" description'
    author = 'A "Quoted" Author'
    result = runner.invoke(cli.app, [
        "My Repo", "python", "--render", str(dest), "--owner", "Some-Org",
        "--author", author, "--description", description, "--year", "2030",
    ])  # fmt: skip
    assert result.exit_code == 0, result.output
    project = tomllib.loads((dest / "pyproject.toml").read_text())["project"]
    assert project["name"] == "my-repo"
    assert project["description"] == description
    assert project["authors"][0]["name"] == author
    assert (dest / "src/my_repo/__init__.py").is_file()
    assert "The owner (`Some-Org`)" in (dest / "README.md").read_text()


@pytest.mark.parametrize(
    "args",
    [
        ["..", "python"],
        ["", "python"],
        ["7up", "rust"],
        ["sample", "cobol"],
        ["sample", "--lib"],
        ["sample", "--owner", "../other"],
        ["sample", "--owner", "a-" * 20 + "a"],
    ],
)
def test_invalid_offline_inputs_create_nothing(tmp_path, args):
    dest = tmp_path / "output"
    result = runner.invoke(cli.app, [*args, "--render", str(dest)])
    assert result.exit_code == 1, result.output
    assert not dest.exists()


def test_render_preserves_existing_destination(tmp_path):
    dest = tmp_path / "output"
    dest.mkdir()
    (dest / "keep.txt").write_text("keep this")
    result = runner.invoke(cli.app, ["sample", "--render", str(dest)])
    assert result.exit_code == 1
    assert "not empty" in result.output
    assert list(dest.iterdir()) == [dest / "keep.txt"]
    assert (dest / "keep.txt").read_text() == "keep this"


@pytest.mark.parametrize("option", ["--owner", "--author", "--description", "--year"])
def test_fixed_render_metadata_requires_render(option):
    result = runner.invoke(cli.app, ["sample", option, "test"])
    assert result.exit_code == 1
    assert "require --render" in result.output


@pytest.mark.parametrize("option", ["-h", "--help", "--version", "--render"])
def test_offline_entrypoint_works_without_git_or_gh(tmp_path, option):
    dest = tmp_path / "output"
    args = [option, str(dest)] if option == "--render" else [option]
    result = subprocess.run(
        [sys.executable, "-c", "from makeghrepo import main; main()", *args],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": "", "MAKEGHREPO_DIR": str(tmp_path / "unused")},
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "unused").exists()
    if option == "--render":
        assert (dest / "README.md").is_file()
        assert not (dest / ".git").exists()
    else:
        assert not dest.exists()


@pytest.mark.parametrize("option", ["--help", "--version"])
def test_help_and_version_do_not_start_external_commands(tmp_path, option):
    code = """
import subprocess

def forbidden(*args, **kwargs):
    raise AssertionError('help/version started an external command')

subprocess.Popen = forbidden
from makeghrepo import main
main()
"""
    result = subprocess.run(
        [sys.executable, "-c", code, option],
        capture_output=True,
        text=True,
        env={**os.environ, "MAKEGHREPO_DIR": str(tmp_path / "unused")},
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "unused").exists()


@pytest.mark.parametrize("no_ci,no_pr", [(False, False), (True, False), (False, True), (True, True)])
@pytest.mark.parametrize("languages", [[], ["python", "rust", "docker"]])
@pytest.mark.parametrize("license_choice", ["none", "MIT"])
def test_offline_creation_policy_keeps_workflows_and_license(tmp_path, no_ci, no_pr, languages, license_choice):
    dest = tmp_path / "output"
    flags = [*(["--no-ci"] if no_ci else []), *(["--no-pr"] if no_pr else [])]
    result = runner.invoke(cli.app, ["sample", *languages, *flags, "--license", license_choice, "--render", str(dest)])
    assert result.exit_code == 0, result.output
    assert (dest / "LICENSE").exists() is (license_choice == "MIT")
    assert (dest / ".github/workflows/ci.yml").exists()
    assert (dest / ".github/workflows/codeql.yml").exists()
    agents = (dest / "AGENTS.md").read_text()
    assert ("Agents must work on a branch and open a PR" in agents) is not no_pr
    assert ("The `ci` check must pass" in agents) is not no_ci
    assert (dest / "CLAUDE.md").read_text() == "@AGENTS.md\n"
    assert not (dest / ".git").exists()


@pytest.mark.parametrize("option", ["--no-ci", "--no-pr"])
def test_private_offline_opt_out_is_rejected_before_writing(tmp_path, option):
    dest = tmp_path / "output"
    result = runner.invoke(cli.app, ["sample", "--private", option, "--render", str(dest)])
    assert result.exit_code == 1 and "only apply to public" in result.output
    assert not dest.exists()


def test_help_describes_independent_public_creation_options():
    result = runner.invoke(cli.app, ["--help"])
    text = Text.from_ansi(result.output).plain
    assert "--no-ci" in text and "--no-pr" in text
    assert "Public only" in text and "workflows" in text
