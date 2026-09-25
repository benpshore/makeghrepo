import git
import pytest
from typer.testing import CliRunner

from makeghrepo import github, scaffold
from makeghrepo.cli import app

runner = CliRunner()


@pytest.fixture
def gh(tmp_path, monkeypatch):
    """Fake GitHub: records calls; repos in `existing` already exist."""
    state = {"calls": [], "existing": set(), "failed": []}
    monkeypatch.setenv("MAKEGHREPO_DIR", str(tmp_path))
    monkeypatch.setattr(github, "current_user", lambda: "me")
    monkeypatch.setattr(github, "repo_exists", lambda r: r in state["existing"])
    monkeypatch.setattr(github, "is_private", lambda r: False)
    monkeypatch.setattr(github, "create_repo", lambda *a: state["calls"].append(("create", *a)))

    def configure_all(repo, *, private, push, log):
        state["calls"].append(("configure", repo, private, push is not None))
        return state["failed"]

    monkeypatch.setattr(github, "configure_all", configure_all)
    monkeypatch.setattr(scaffold, "smoke_test", lambda *a: None)  # covered in test_template
    return state


def test_name_and_languages(tmp_path, gh):
    result = runner.invoke(app, ["My Repo", "python", "c++"])
    assert result.exit_code == 0, result.output
    repo = git.Repo(tmp_path / "my-repo")
    assert [c.message.strip() for c in repo.iter_commits()] == ["setup"]
    assert repo.active_branch.name == "main" and not repo.is_dirty(untracked_files=True)
    tracked = repo.git.ls_files()
    assert "pyproject.toml" in tracked and "src/main.cpp" in tracked
    assert gh["calls"][0][:3] == ("create", "me/my-repo", tmp_path / "my-repo")
    assert gh["calls"][1] == ("configure", "me/my-repo", False, True)


def test_no_args_random_name_any_language(tmp_path, gh):
    result = runner.invoke(app, [])
    assert result.exit_code == 0, result.output
    (created,) = tmp_path.iterdir()
    assert "-" in created.name
    assert not (created / "pyproject.toml").exists()


def test_language_first_means_random_name(tmp_path, gh):
    result = runner.invoke(app, ["rust"])
    assert result.exit_code == 0, result.output
    (created,) = tmp_path.iterdir()
    assert created.name != "rust" and (created / "Cargo.toml").exists()


def test_private(tmp_path, gh):
    result = runner.invoke(app, ["secret", "python", "--private"])
    assert result.exit_code == 0, result.output
    assert gh["calls"][0][-1] is True
    assert not (tmp_path / "secret/.github/workflows/codeql.yml").exists()


def test_unknown_language(tmp_path, gh):
    result = runner.invoke(app, ["x", "cobol"])
    assert result.exit_code == 1
    assert "cobol" in result.output
    assert not (tmp_path / "x").exists()


def test_rerun_resumes_without_rescaffolding(tmp_path, gh):
    assert runner.invoke(app, ["again", "python"]).exit_code == 0
    gh["existing"].add("me/again")
    gh["calls"].clear()
    result = runner.invoke(app, ["again"])
    assert result.exit_code == 0, result.output
    assert "resuming" in result.output
    # repo exists on GitHub: no create, just re-apply settings (push since main isn't there)
    assert [c[0] for c in gh["calls"]] == ["configure"]
    assert len(list(git.Repo(tmp_path / "again").iter_commits())) == 1


def test_existing_non_git_dir_is_refused(tmp_path, gh):
    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "file").write_text("x")
    assert runner.invoke(app, ["taken"]).exit_code == 1
    assert gh["calls"] == []


def test_exists_on_github_only_is_refused(tmp_path, gh):
    gh["existing"].add("me/remote-only")
    assert runner.invoke(app, ["remote-only"]).exit_code == 1
    assert not (tmp_path / "remote-only").exists()


def test_failed_steps_exit_2_with_rerun_hint(tmp_path, gh):
    gh["failed"] = ["project board"]
    result = runner.invoke(app, ["partial"])
    assert result.exit_code == 2
    assert "re-run: makeghrepo partial" in result.output
