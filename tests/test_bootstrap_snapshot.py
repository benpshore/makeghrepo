"""Publication safety with real local Git and no GitHub or network effects."""

import gitcli
import pytest
from typer.testing import CliRunner

from makeghrepo import github, gitops, scaffold
from makeghrepo.cli import app

runner = CliRunner()


def commit(path, name="later.txt"):
    (path / name).write_text(name)
    gitops.commit_all(path, name)
    return gitcli.out(path, "rev-parse", "HEAD")


@pytest.fixture
def local_remote(tmp_path):
    dest, remote = tmp_path / "local", tmp_path / "remote.git"
    gitops.init(dest)
    gitcli.init(remote, "--bare")
    gitcli.git(dest, "remote", "add", "origin", str(remote))
    commit(dest, "first.txt")
    return dest, remote


def test_push_creates_exact_validated_snapshot_and_upstream(local_remote):
    dest, remote = local_remote
    sha = gitops.bootstrap_head(dest)
    gitops.push_main(dest, sha)
    assert gitcli.out(remote, "rev-parse", "refs/heads/main") == sha
    assert gitcli.out(dest, "rev-parse", "@{upstream}") == sha


@pytest.mark.parametrize("change", ["dirty", "staged", "untracked", "branch", "detached", "commit"])
def test_local_drift_blocks_publication(local_remote, change):
    dest, remote = local_remote
    sha = gitops.bootstrap_head(dest)
    if change in {"dirty", "staged"}:
        (dest / "first.txt").write_text("different")
        if change == "staged":
            gitcli.git(dest, "add", "first.txt")
    elif change == "untracked":
        (dest / "new.txt").write_text("untracked")
    elif change == "branch":
        gitcli.git(dest, "checkout", "-b", "different")
    elif change == "detached":
        gitcli.git(dest, "checkout", "--detach")
    else:
        commit(dest)
    with pytest.raises(RuntimeError, match=r"bootstrap|main changed"):
        gitops.push_main(dest, sha)
    assert gitcli.git(remote, "rev-parse", "--verify", "main", check=False).returncode != 0


def test_late_commit_after_local_check_cannot_replace_frozen_push_source(local_remote, monkeypatch):
    dest, remote = local_remote
    sha = gitops.bootstrap_head(dest)
    original = gitops.require_bootstrap_head

    def late_commit(path, expected):
        original(path, expected)
        commit(path)

    monkeypatch.setattr(gitops, "require_bootstrap_head", late_commit)
    gitops.push_main(dest, sha)
    assert gitcli.out(dest, "rev-parse", "HEAD") != sha
    assert gitcli.out(remote, "rev-parse", "main") == sha


def test_concurrent_existing_ancestor_main_is_never_advanced(local_remote):
    dest, remote = local_remote
    first = gitops.bootstrap_head(dest)
    assert not gitops.remote_has_main(dest)
    second = commit(dest)
    gitcli.git(dest, "push", "origin", f"{first}:refs/heads/main")
    with pytest.raises(RuntimeError, match=r"stale info|rejected"):
        gitops.push_main(dest, second)
    assert gitcli.out(remote, "rev-parse", "main") == first


def test_identical_concurrent_main_is_verified_without_claiming_creation(local_remote):
    dest, remote = local_remote
    sha = gitops.bootstrap_head(dest)
    gitcli.git(dest, "push", "origin", "main")
    gitops.push_main(dest, sha)
    assert gitcli.out(remote, "rev-parse", "main") == sha


@pytest.fixture
def bootstrap(tmp_path, monkeypatch):
    """Real CLI/Git, inert render/gh, with an explicit test-only transport adapter."""
    remote = tmp_path / "remote.git"
    gitcli.init(remote, "--bare")
    state = {"created": False, "failed": False, "smoke": [], "configured": 0, "before_push": None}
    dest = tmp_path / "r"
    monkeypatch.setenv("MAKEGHREPO_DIR", str(tmp_path))
    monkeypatch.delenv("MAKEGHREPO_OWNER", raising=False)
    monkeypatch.setattr(github, "current_user", lambda: "me")
    monkeypatch.setattr(github, "repo_exists", lambda _: state["created"])
    monkeypatch.setattr(github, "is_private", lambda _: False)
    monkeypatch.setattr(github, "prepare_configuration", lambda *a, **k: object())

    def render(path, data):
        path.mkdir()
        (path / "source.txt").write_text("source")

    def create(*args):
        state["created"] = True
        gitcli.git(dest, "remote", "add", "origin", "https://github.com/me/r.git")
        if state.get("after_create"):
            state["after_create"]()

    def smoke(path, *_):
        state["smoke"].append(gitcli.git(path, "rev-parse", "HEAD", check=False).stdout.strip())
        if state.get("smoke_mutation"):
            state["smoke_mutation"]()

    def configure(*args, push, **kwargs):
        state["configured"] += 1
        if state["failed"]:
            return ["controlled setup failure", "push main"]
        if state["before_push"]:
            state["before_push"]()
        if push:
            try:
                push()
            except RuntimeError:
                return ["push main"]
        return []

    # The validation path sees the canonical real origin, while only the test
    # transport sends Git push/ls-remote to a disposable bare repo.
    real_git = gitops._git

    def transport(*args, **kwargs):
        if args[0] in {"push", "ls-remote"}:
            args = ("-c", f"url.{remote.as_uri()}.insteadOf=https://github.com/me/r.git", *args)
        return real_git(*args, **kwargs)

    monkeypatch.setattr(gitops, "_git", transport)
    monkeypatch.setattr(scaffold, "render", render)
    monkeypatch.setattr(scaffold, "smoke_test", smoke)
    monkeypatch.setattr(github, "create_repo", create)
    monkeypatch.setattr(github, "configure_all", configure)
    return state, dest, remote


def test_unpublished_retry_retests_later_commit_before_exact_push(bootstrap):
    state, dest, remote = bootstrap
    state["failed"] = True
    assert runner.invoke(app, ["r"]).exit_code == 2
    later = commit(dest)
    state["failed"] = False
    state["smoke"].clear()
    result = runner.invoke(app, ["r"])
    assert result.exit_code == 0, result.output
    assert state["smoke"] == [later]
    assert gitcli.out(remote, "rev-parse", "main") == later


@pytest.mark.parametrize("resume", [False, True])
def test_commit_after_configuration_readback_does_not_publish(bootstrap, resume):
    state, dest, remote = bootstrap
    if resume:
        state["failed"] = True
        assert runner.invoke(app, ["r"]).exit_code == 2
        state["failed"] = False
    state["before_push"] = lambda: commit(dest)
    result = runner.invoke(app, ["r"])
    assert result.exit_code == 2, result.output
    assert gitcli.git(remote, "rev-parse", "--verify", "main", check=False).returncode != 0
    assert gitops.read_marker(dest) is not None


@pytest.mark.parametrize("change", ["commit", "dirty", "untracked", "branch"])
def test_snapshot_changed_by_smoke_check_blocks_configuration(bootstrap, change):
    state, dest, _remote = bootstrap
    state["failed"] = True
    assert runner.invoke(app, ["r"]).exit_code == 2
    configured = state["configured"]
    state["failed"] = False

    def mutate():
        if change == "commit":
            commit(dest)
        elif change == "dirty":
            (dest / "source.txt").write_text("changed")
        elif change == "untracked":
            (dest / "untracked.txt").write_text("changed")
        else:
            gitcli.git(dest, "checkout", "-b", "other")

    state["smoke_mutation"] = mutate
    result = runner.invoke(app, ["r"])
    assert result.exit_code == 1, result.output
    assert "snapshot validation failed" in result.output
    assert state["configured"] == configured
    assert not gitops.remote_has_main(dest)


def test_settings_only_resume_neither_tests_nor_pushes_later_dirty_work(bootstrap):
    state, dest, remote = bootstrap
    assert runner.invoke(app, ["r"]).exit_code == 0
    published = gitcli.out(remote, "rev-parse", "main")
    commit(dest)
    (dest / "untracked.txt").write_text("local")
    state["smoke"].clear()
    result = runner.invoke(app, ["r"])
    assert result.exit_code == 0, result.output
    assert not state["smoke"]
    assert gitcli.out(remote, "rev-parse", "main") == published


@pytest.mark.parametrize("redirection", ["rewrite", "pushurl", "multiple-pushurls"])
def test_fresh_origin_redirection_stops_before_configuration(bootstrap, tmp_path, redirection):
    state, dest, remote = bootstrap
    unexpected = tmp_path / "unexpected.git"
    gitcli.init(unexpected, "--bare")

    def redirect():
        if redirection == "rewrite":
            gitcli.git(
                dest, "config", f"url.{unexpected.as_uri()}.insteadOf", "https://github.com/"
            )
        else:
            if redirection == "multiple-pushurls":
                gitcli.git(
                    dest, "config", "--add", "remote.origin.pushurl", "https://github.com/me/r.git"
                )
            gitcli.git(dest, "config", "--add", "remote.origin.pushurl", str(unexpected))

    state["after_create"] = redirect
    result = runner.invoke(app, ["r"])
    assert result.exit_code == 1, result.output
    assert "Origin validation failed" in result.output
    assert state["created"] and state["configured"] == 0
    for repo in (remote, unexpected):
        assert gitcli.git(repo, "rev-parse", "--verify", "main", check=False).returncode != 0


def test_fresh_bootstrap_runs_checks_once(bootstrap):
    state, _dest, _remote = bootstrap
    result = runner.invoke(app, ["r"])
    assert result.exit_code == 0, result.output
    assert len(state["smoke"]) == 1


def test_commit_hook_cannot_substitute_unchecked_tree(tmp_path):
    gitops.init(tmp_path)
    (tmp_path / "source.txt").write_text("checked")
    hook = tmp_path / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nprintf 'unchecked\\n' > source.txt\ngit add source.txt\n")
    hook.chmod(0o755)
    with pytest.raises(RuntimeError, match="commit changed the staged tree"):
        gitops.commit_all(tmp_path)
    assert gitops.has_commits(tmp_path)  # retained for a genuine checked resume


@pytest.mark.parametrize("unavailable", [False, True])
def test_post_push_readback_failure_is_not_claimed_unpublished(
    local_remote, monkeypatch, unavailable
):
    dest, remote = local_remote
    sha = gitops.bootstrap_head(dest)

    def readback(_):
        if unavailable:
            raise gitops.GitError(("ls-remote",), 128, "controlled readback failure")
        return None

    monkeypatch.setattr(gitops, "remote_main", readback)
    with pytest.raises(RuntimeError, match="publication may have completed"):
        gitops.push_main(dest, sha)
    assert gitcli.out(remote, "rev-parse", "main") == sha


def test_upstream_failure_reports_already_verified_publication(local_remote, monkeypatch):
    dest, remote = local_remote
    sha = gitops.bootstrap_head(dest)
    original = gitops._git

    def broken(*args, **kwargs):
        if args[:2] == ("branch", "--set-upstream-to=origin/main"):
            raise gitops.GitError(args, 128, "controlled local configuration failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(gitops, "_git", broken)
    with pytest.raises(
        RuntimeError, match=f"remote main was verified at {sha}; only local upstream"
    ):
        gitops.push_main(dest, sha)
    assert gitcli.out(remote, "rev-parse", "main") == sha


def test_push_lost_ack_reports_observed_effect_and_never_replays(local_remote, monkeypatch):
    dest, remote = local_remote
    sha = gitops.bootstrap_head(dest)
    original = gitops._git
    pushes = []

    def lost_ack(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[0] == "push":
            pushes.append(args)
            raise gitops.GitError(args, 128, "connection lost after accepting ref")
        return result

    monkeypatch.setattr(gitops, "_git", lost_ack)
    with pytest.raises(RuntimeError, match=f"remote main is currently observed at {sha}"):
        gitops.push_main(dest, sha)
    assert len(pushes) == 1
    assert gitops.remote_has_main(dest)
    assert gitcli.out(remote, "rev-parse", "main") == sha
