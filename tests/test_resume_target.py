import git
import pytest
from typer.testing import CliRunner

from makeghrepo import github, gitops, scaffold
from makeghrepo.cli import app


@pytest.mark.parametrize("changed", ["owner", "name", "origin", "pushurl"])
def test_resume_refuses_a_different_target(tmp_path, monkeypatch, changed):
    dest = tmp_path / "sample"
    gitops.init(dest)
    gitops.write_marker(dest, {
        "schema": 1, "owner": "me", "name": "sample", "private": True, "languages": [],
    })  # fmt: skip
    repo = git.Repo(dest)
    repo.create_remote("origin", "https://github.com/me/sample.git")
    owner = "me"
    name = "sample"
    if changed == "owner":
        owner = "someone-else"
    elif changed == "name":
        name = "renamed"
        dest.rename(tmp_path / name)
    elif changed == "origin":
        repo.git.remote("set-url", "origin", "https://github.com/other/public.git")
    else:
        repo.git.config("remote.origin.pushurl", "https://github.com/other/public.git")

    monkeypatch.setenv("MAKEGHREPO_DIR", str(tmp_path))
    monkeypatch.setattr(github, "current_user", lambda: owner)
    monkeypatch.setattr(github, "repo_exists", lambda _: True)
    reached = []
    monkeypatch.setattr(scaffold, "smoke_test", lambda *a: reached.append("checks"))
    monkeypatch.setattr(gitops, "commit_all", lambda *a: reached.append("commit"))
    monkeypatch.setattr(github, "is_private", lambda _: True)
    monkeypatch.setattr(gitops, "remote_has_main", lambda _: False)
    monkeypatch.setattr(github, "configure_all", lambda *a, **kw: reached.append("configure") or [])
    result = CliRunner().invoke(app, [name])
    assert result.exit_code == 1, result.output
    assert "does not match" in result.output
    assert reached == []


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/me/sample.git",
        "https://github.com/me/sample",
        "git@github.com:me/sample.git",
        "ssh://git@github.com/me/sample.git",
        "ssh://git@github.com:22/me/sample.git",
        "ssh://git@ssh.github.com:443/me/sample.git",
        "https://github.com/ME/SAMPLE.git/",
    ],
)
def test_matching_origin_is_accepted(tmp_path, url):
    repo = git.Repo.init(tmp_path)
    repo.create_remote("origin", url)
    gitops.validate_origin(tmp_path, "me/sample")


def test_absent_origin_after_failed_create_is_allowed(tmp_path):
    gitops.init(tmp_path)
    gitops.validate_origin(tmp_path, "me/sample")


@pytest.mark.parametrize("override", ["pushurl", "insteadOf", "pushInsteadOf"])
def test_effective_push_destination_is_checked(tmp_path, override):
    repo = git.Repo.init(tmp_path)
    repo.create_remote("origin", "https://github.com/me/sample.git")
    if override == "pushurl":
        repo.git.config("--add", "remote.origin.pushurl", "git@github.com:me/sample.git")
        repo.git.config("--add", "remote.origin.pushurl", "git@github.com:other/public.git")
    else:
        repo.git.config(f"url.https://github.com/other/.{override}", "https://github.com/me/")
    with pytest.raises(RuntimeError, match="origin does not match"):
        gitops.validate_origin(tmp_path, "me/sample")
