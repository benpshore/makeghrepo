import git
import pytest

from makeghrepo import gitops


def test_ensure_identity_lets_commit_succeed_without_any_host_identity(tmp_path, monkeypatch):
    """A fresh host/CI runner with no git identity configured anywhere must not block a commit."""
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.delenv(f"GIT_{role}_NAME", raising=False)
        monkeypatch.delenv(f"GIT_{role}_EMAIL", raising=False)
    gitops.init(tmp_path)
    repo = git.Repo(tmp_path)
    (tmp_path / "f.txt").write_text("x")
    repo.git.add(all=True)
    with pytest.raises(git.GitCommandError):
        repo.git.commit("-m", "test")

    gitops.ensure_identity(tmp_path, "Ada Lovelace", "ada@users.noreply.github.com")
    repo.git.commit("-m", "test")
    assert repo.head.commit.author.name == "Ada Lovelace"
    assert repo.head.commit.author.email == "ada@users.noreply.github.com"


def test_ensure_identity_never_overrides_a_real_one(tmp_path):
    gitops.init(tmp_path)
    repo = git.Repo(tmp_path)
    with repo.config_writer() as writer:
        writer.set_value("user", "name", "Real Person")
        writer.set_value("user", "email", "real@example.com")

    gitops.ensure_identity(tmp_path, "Fallback", "fallback@users.noreply.github.com")

    reader = repo.config_reader()
    assert reader.get_value("user", "name") == "Real Person"
    assert reader.get_value("user", "email") == "real@example.com"
