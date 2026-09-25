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


def test_github_flow_order(tmp_path, monkeypatch):
    """new: render, commit, create repo (no push), then configure_all with a push step."""
    seen = []
    monkeypatch.setattr(github, "current_user", lambda: "me")
    monkeypatch.setattr(github, "repo_exists", lambda r: False)
    monkeypatch.setattr(github, "create_repo", lambda *a: seen.append(("create", a)))

    def fake_configure_all(repo, *, private, project, push, log):
        seen.append(("configure", repo, private))
        return []

    monkeypatch.setattr(github, "configure_all", fake_configure_all)
    result = runner.invoke(
        app, ["new", "gh-flow", "--private", "--skip-checks", "-y", "--dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert seen[0][0] == "create" and seen[0][1][0] == "me/gh-flow"
    assert seen[1] == ("configure", "me/gh-flow", True)
    ls = git.Repo(tmp_path / "gh-flow").git.ls_files()
    assert "codeql.yml" not in ls  # no code scanning on GitHub Free private repos


def test_configure_requires_owner_repo():
    assert runner.invoke(app, ["configure", "nope"]).exit_code == 1


def test_rejects_flag_like_owner(tmp_path):
    result = runner.invoke(app, ["new", "x", "--owner=-evil", "-y", "--dir", str(tmp_path)])
    assert result.exit_code == 1
    assert not (tmp_path / "x").exists()


def test_rejects_multiline_description(tmp_path):
    result = runner.invoke(
        app, ["new", "x", "--local", "-y", "-d", 'a"\n[tool.uv]', "--dir", str(tmp_path)]
    )
    assert result.exit_code == 1
    assert not (tmp_path / "x").exists()


def test_configure_rejects_traversal():
    assert runner.invoke(app, ["configure", "../user"]).exit_code == 1
