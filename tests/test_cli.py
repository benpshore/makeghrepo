import json

import git
import pytest
from typer.testing import CliRunner

from makeghrepo import github, gitops, scaffold
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
    monkeypatch.setattr(github, "prepare_configuration", lambda owner, private: (owner, private))
    monkeypatch.setattr(gitops, "remote_has_main", lambda _: False)

    def configure_all(repo, *, private, configuration, push, log):
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


def test_configuration_is_prepared_before_remote_creation(tmp_path, gh, monkeypatch):
    events = []

    def prepare(owner, private):
        events.append("prepare")
        return object()

    monkeypatch.setattr(github, "prepare_configuration", prepare)
    monkeypatch.setattr(github, "create_repo", lambda *args: events.append("create"))
    monkeypatch.setattr(
        github,
        "configure_all",
        lambda *args, **kwargs: events.append("configure") or [],
    )

    result = runner.invoke(app, ["ordered", "python"])

    assert result.exit_code == 0, result.output
    assert events == ["prepare", "create", "configure"]


def test_configuration_preflight_failure_does_not_create_remote(tmp_path, gh, monkeypatch):
    def fail_preflight(*args):
        raise github.GhError("invalid owner identity")

    monkeypatch.setattr(github, "prepare_configuration", fail_preflight)

    result = runner.invoke(app, ["no-remote", "python"])

    assert result.exit_code == 1
    assert "No GitHub repository was created" in result.output
    assert "Local project is intact" in result.output
    assert gh["calls"] == []
    assert gitops.read_marker(tmp_path / "no-remote") is not None
    assert len(list(git.Repo(tmp_path / "no-remote").iter_commits())) == 1


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


@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize("choice", [None, "none", "MIT"])
def test_license_is_opt_in_and_recorded(tmp_path, gh, private, choice):
    args = ["licensing", "python"]
    if private:
        args += ["--private"]
    if choice is not None:
        args += ["--license", choice]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    dest = tmp_path / "licensing"
    assert gitops.read_marker(dest)["license"] == (choice or "none")
    assert (dest / "LICENSE").exists() is (choice == "MIT")


def test_unsupported_license_is_rejected_before_github(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(github, "current_user", lambda: calls.append("user"))
    result = runner.invoke(app, ["licensing", "--license", "arbitrary"])
    assert result.exit_code == 2
    assert "arbitrary" in result.output
    assert calls == []
    assert not (tmp_path / "licensing").exists()


@pytest.mark.parametrize("choice", ["none", "MIT"])
@pytest.mark.parametrize("explicit", [False, True])
def test_resume_preserves_license_files_and_recorded_choice(tmp_path, gh, choice, explicit):
    assert runner.invoke(app, ["licensing", "--license", choice]).exit_code == 0
    dest = tmp_path / "licensing"
    license_path = dest / "LICENSE"
    license_path.write_text("Custom terms added after bootstrap.\n")
    marker_before = (dest / ".git/makeghrepo.json").read_bytes()
    args = ["licensing", "--license", choice] if explicit else ["licensing"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert license_path.read_text() == "Custom terms added after bootstrap.\n"
    assert (dest / ".git/makeghrepo.json").read_bytes() == marker_before


@pytest.mark.parametrize("choice,requested", [("none", "MIT"), ("MIT", "none")])
def test_resume_refuses_an_explicit_license_change(tmp_path, gh, choice, requested):
    assert runner.invoke(app, ["licensing", "--license", choice]).exit_code == 0
    gh["calls"].clear()
    result = runner.invoke(app, ["licensing", "--license", requested])
    assert result.exit_code == 1
    assert "a resume never changes licensing" in result.output
    assert gh["calls"] == []
    assert (tmp_path / "licensing/LICENSE").exists() is (choice == "MIT")


def test_resume_legacy_marker_retains_mit(tmp_path, gh):
    assert runner.invoke(app, ["licensing", "--license", "MIT"]).exit_code == 0
    dest = tmp_path / "licensing"
    marker = gitops.read_marker(dest)
    del marker["license"]
    gitops.write_marker(dest, marker)
    license_before = (dest / "LICENSE").read_bytes()
    assert runner.invoke(app, ["licensing"]).exit_code == 0
    assert (dest / "LICENSE").read_bytes() == license_before
    assert "license" not in gitops.read_marker(dest)
    gh["calls"].clear()
    result = runner.invoke(app, ["licensing", "--license", "none"])
    assert result.exit_code == 1
    assert "was created with license MIT" in result.output
    assert gh["calls"] == []


@pytest.mark.parametrize("invalid", [None, False, "arbitrary"])
def test_resume_rejects_an_invalid_recorded_license(tmp_path, gh, invalid):
    assert runner.invoke(app, ["licensing"]).exit_code == 0
    dest = tmp_path / "licensing"
    gitops.write_marker(dest, {**gitops.read_marker(dest), "license": invalid})
    gh["calls"].clear()
    result = runner.invoke(app, ["licensing"])
    assert result.exit_code == 1
    assert "invalid recorded license" in result.output
    assert gh["calls"] == []


def test_lib_flag_requires_python(tmp_path, gh):
    result = runner.invoke(app, ["x", "rust", "--lib"])
    assert result.exit_code == 1
    assert "--lib" in result.output
    assert not (tmp_path / "x").exists()


def test_lib_flag(tmp_path, gh):
    result = runner.invoke(app, ["nolib", "python", "--lib"])
    assert result.exit_code == 0, result.output
    pyproject = (tmp_path / "nolib/pyproject.toml").read_text()
    assert "[project.scripts]" not in pyproject
    assert (tmp_path / "nolib/src/nolib/py.typed").exists()


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


def test_postcreation_settings_failure_keeps_remote_for_retry(tmp_path, gh):
    gh["failed"] = ["ruleset 'protect-main'", "push main"]

    result = runner.invoke(app, ["settings-failed", "python"])

    assert result.exit_code == 2
    assert gh["calls"][0][0] == "create"
    assert gh["calls"][1][0] == "configure"
    assert "repository was created but setup did not finish" in result.output
    assert "does not delete it" in result.output
    assert "re-run: makeghrepo settings-failed" in result.output


def test_failed_check_then_rerun_finishes_the_commit(tmp_path, gh, monkeypatch):
    def broken(*a):
        raise RuntimeError("ruff failed")

    monkeypatch.setattr(scaffold, "smoke_test", broken)
    result = runner.invoke(app, ["fixme", "python"])
    assert result.exit_code == 1 and "re-run the same command" in result.output
    assert gh["calls"] == []  # nothing published

    monkeypatch.setattr(scaffold, "smoke_test", lambda *a: None)  # user fixed it
    result = runner.invoke(app, ["fixme", "python"])
    assert result.exit_code == 0, result.output
    assert [c.message.strip() for c in git.Repo(tmp_path / "fixme").iter_commits()] == ["setup"]
    assert gh["calls"][0][0] == "create"


def test_rerun_after_failed_create_keeps_private(tmp_path, gh, monkeypatch):
    def create_fails(*a):
        raise github.GhError("network down")

    monkeypatch.setattr(github, "create_repo", create_fails)
    assert runner.invoke(app, ["hush", "--private"]).exit_code == 1

    monkeypatch.setattr(github, "create_repo", lambda *a: gh["calls"].append(("create", *a)))
    result = runner.invoke(app, ["hush"])  # --private forgotten on the re-run
    assert result.exit_code == 0, result.output
    assert gh["calls"][0][-1] is True


def _fail_create(monkeypatch):
    def create_fails(*a):
        raise github.GhError("network down")

    monkeypatch.setattr(github, "create_repo", create_fails)


def test_create_writes_the_resume_marker(tmp_path, gh):
    assert runner.invoke(app, ["marked", "python", "--private"]).exit_code == 0
    marker = gitops.read_marker(tmp_path / "marked")
    assert marker is not None
    assert marker["name"] == "marked" and marker["owner"] == "me"
    assert marker["private"] is True and marker["languages"] == ["python"]
    assert marker["lib"] is False and str(marker["created_by"]).startswith("makeghrepo ")
    assert "makeghrepo.json" not in git.Repo(tmp_path / "marked").git.ls_files()


def test_unrelated_git_repo_is_refused(tmp_path, gh):
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    git.Repo.init(foreign)
    (foreign / "secrets.txt").write_text("not for github")
    result = runner.invoke(app, ["foreign"])
    assert result.exit_code == 1
    assert "didn't create" in result.output
    assert gh["calls"] == []


def test_corrupt_marker_is_refused(tmp_path, gh, monkeypatch):
    _fail_create(monkeypatch)
    assert runner.invoke(app, ["broken"]).exit_code == 1
    (tmp_path / "broken" / ".git" / "makeghrepo.json").write_text("{not json")
    result = runner.invoke(app, ["broken"])
    assert result.exit_code == 1 and "didn't create" in result.output
    assert gh["calls"] == []


def test_private_flag_on_a_repo_created_public_is_refused(tmp_path, gh, monkeypatch):
    _fail_create(monkeypatch)
    assert runner.invoke(app, ["open"]).exit_code == 1
    result = runner.invoke(app, ["open", "--private"])
    assert result.exit_code == 1
    assert "was created public" in result.output
    assert gh["calls"] == []


def test_private_flag_on_a_public_github_repo_is_refused(tmp_path, gh):
    assert runner.invoke(app, ["shown"]).exit_code == 0
    gh["existing"].add("me/shown")
    marker = tmp_path / "shown" / ".git" / "makeghrepo.json"
    data = json.loads(marker.read_text())
    marker.write_text(json.dumps({**data, "private": True}))  # meant private; GitHub says public
    gh["calls"].clear()
    result = runner.invoke(app, ["shown", "--private"])
    assert result.exit_code == 1
    assert "public on GitHub" in result.output
    assert gh["calls"] == []


def test_resume_uses_the_marker_languages(tmp_path, gh, monkeypatch):
    seen = []

    def broken(dest, langs, log):
        seen.append(list(langs))
        raise RuntimeError("check failed")

    monkeypatch.setattr(scaffold, "smoke_test", broken)
    assert runner.invoke(app, ["polyglot", "python"]).exit_code == 1
    result = runner.invoke(app, ["polyglot", "rust"])
    assert result.exit_code == 1
    assert "ignoring languages on resume; using python" in result.output
    assert seen == [["python"], ["python"]]


@pytest.mark.parametrize("remote_has_main", [False, True])
def test_resume_private_intent_refuses_public_remote_without_flag(
    tmp_path, gh, monkeypatch, remote_has_main
):
    assert runner.invoke(app, ["hush", "--private"]).exit_code == 0
    gh["existing"].add("me/hush")
    gh["calls"].clear()
    monkeypatch.setattr(gitops, "remote_has_main", lambda _: remote_has_main)
    result = runner.invoke(app, ["hush"])
    assert result.exit_code == 1
    assert "public on GitHub" in result.output
    assert gh["calls"] == []


def test_resume_private_intent_accepts_private_remote_without_flag(tmp_path, gh, monkeypatch):
    assert runner.invoke(app, ["hush", "--private"]).exit_code == 0
    gh["existing"].add("me/hush")
    gh["calls"].clear()
    monkeypatch.setattr(github, "is_private", lambda _: True)
    result = runner.invoke(app, ["hush"])
    assert result.exit_code == 0, result.output
    assert gh["calls"] == [("configure", "me/hush", True, True)]


def test_rust_refuses_a_name_starting_with_a_digit(tmp_path, gh):
    result = runner.invoke(app, ["7up", "rust"])
    assert result.exit_code == 1
    assert "must start with a letter" in result.output
    assert not (tmp_path / "7up").exists()
    assert gh["calls"] == []


def test_python_accepts_a_name_starting_with_a_digit(tmp_path, gh):
    assert runner.invoke(app, ["7up", "python"]).exit_code == 0
    assert (tmp_path / "7up" / "src" / "_7up").is_dir()


def test_resume_lookup_failure_never_pushes_or_configures(tmp_path, gh, monkeypatch):
    assert runner.invoke(app, ["again"]).exit_code == 0
    gh["existing"].add("me/again")
    gh["calls"].clear()
    dest = tmp_path / "again"
    (dest / "later.txt").write_text("local work that must not be published")
    gitops.commit_all(dest, "later local work")
    before = git.Repo(dest).head.commit.hexsha

    def unreachable(_):
        raise git.GitCommandError("git ls-remote", 128, stderr="temporary connection failure")

    monkeypatch.setattr(gitops, "remote_has_main", unreachable)
    result = runner.invoke(app, ["again"])
    assert result.exit_code == 1
    assert "Could not check remote main; refusing to push" in result.output
    assert gh["calls"] == []
    assert git.Repo(dest).head.commit.hexsha == before
