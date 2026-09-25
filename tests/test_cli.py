import git
from typer.testing import CliRunner

from makeghrepo import github
from makeghrepo.cli import app

runner = CliRunner()


def test_local_new_creates_single_setup_commit(tmp_path):
    result = runner.invoke(
        app, ["new", "My Repo", "--local", "--skip-checks", "-y", "--dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    repo = git.Repo(tmp_path / "my-repo")
    assert repo.active_branch.name == "main"
    commits = list(repo.iter_commits())
    assert [c.message.strip() for c in commits] == ["setup"]
    assert not repo.is_dirty(untracked_files=True)
    tracked = repo.git.ls_files().splitlines()
    assert ".github/workflows/ci.yml" in tracked
    assert "src/my_repo/__init__.py" in tracked


def test_random_name_when_omitted(tmp_path):
    result = runner.invoke(app, ["new", "--local", "--skip-checks", "-y", "--dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    (created,) = tmp_path.iterdir()
    assert "-" in created.name


def test_existing_dir_is_refused(tmp_path):
    (tmp_path / "taken").mkdir()
    result = runner.invoke(app, ["new", "taken", "--local", "-y", "--dir", str(tmp_path)])
    assert result.exit_code == 1
    assert list((tmp_path / "taken").iterdir()) == []


def test_declining_confirmation_does_nothing(tmp_path):
    result = runner.invoke(app, ["new", "x", "--local", "--dir", str(tmp_path)], input="n\n")
    assert result.exit_code == 1
    assert not (tmp_path / "x").exists()


def test_dry_run_prints_github_plan(tmp_path, monkeypatch):
    real_run = github.subprocess.run

    def guard(cmd, *a, **k):
        assert cmd[0] != "gh", f"gh must not run in dry-run: {cmd}"
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(github.subprocess, "run", guard)
    result = runner.invoke(
        app,
        ["new", "dry", "--dry-run", "--owner", "me", "--skip-checks", "-y", "--dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert "gh repo create me/dry --public" in result.output
    assert "repos/me/dry/subscription" in result.output


def test_configure_requires_owner_repo():
    result = runner.invoke(app, ["configure", "nope"])
    assert result.exit_code == 1
