from pathlib import Path

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


MARKER = {"schema": 1, "name": "x", "owner": "me", "private": True, "languages": ["python"]}


def test_marker_round_trips_and_stays_out_of_the_work_tree(tmp_path):
    gitops.init(tmp_path)
    gitops.write_marker(tmp_path, MARKER)
    assert gitops.read_marker(tmp_path) == MARKER
    assert (tmp_path / ".git" / "makeghrepo.json").is_file()
    assert git.Repo(tmp_path).untracked_files == []


@pytest.mark.parametrize(
    "content",
    [
        None,  # missing
        "{not json",
        "[]",
        '{"schema": 2, "private": false, "languages": []}',
        '{"schema": 1, "private": "yes", "languages": []}',
        '{"schema": 1, "private": false, "languages": "python"}',
        '{"schema": 1, "private": false, "languages": [1]}',
    ],
)
def test_read_marker_rejects_anything_but_a_valid_schema_1_marker(tmp_path, content):
    gitops.init(tmp_path)
    if content is not None:
        (tmp_path / ".git" / "makeghrepo.json").write_text(content)
    assert gitops.read_marker(tmp_path) is None


@pytest.mark.parametrize(
    ("name", "junk"),
    [
        (".env", True),
        ("app/.env.local", True),
        (".env.example", False),
        ("keys/server.PEM", True),
        ("data/app.sqlite3", True),
        (".DS_Store", True),
        ("src/main.py", False),
        ("docs/keynote.md", False),
    ],
)
def test_junk_files(name, junk):
    assert gitops.junk_files([name]) == ([name] if junk else [])


def test_commit_refuses_junk_and_keeps_nothing_committed(tmp_path):
    gitops.init(tmp_path)
    (tmp_path / "ok.txt").write_text("fine")
    (tmp_path / "deploy.key").write_text("-----BEGIN PRIVATE KEY-----")
    with pytest.raises(RuntimeError, match=r"deploy\.key"):
        gitops.commit_all(tmp_path)
    assert not gitops.has_commits(tmp_path)


def test_junk_pattern_matches_the_generated_ci_check():
    ci = Path(gitops.__file__).parent / "templates/project/template/.github/workflows/ci.yml.jinja"
    assert f"grep -Ei '{gitops.JUNK_PATTERN}'" in ci.read_text()


def test_remote_main_distinguishes_empty_published_and_unreachable(tmp_path):
    dest = tmp_path / "local"
    remote = tmp_path / "remote.git"
    gitops.init(dest)
    repo = git.Repo(dest)
    git.Repo.init(remote, bare=True)
    repo.create_remote("origin", str(remote))
    assert gitops.remote_has_main(dest) is False

    (dest / "ready.txt").write_text("ready")
    gitops.commit_all(dest)
    gitops.push_main(dest, gitops.main_commit(dest).hexsha)
    assert gitops.remote_has_main(dest) is True

    repo.git.remote("set-url", "origin", str(tmp_path / "missing.git"))
    with pytest.raises(git.GitCommandError):
        gitops.remote_has_main(dest)
    repo.git.remote("remove", "origin")
    with pytest.raises(git.GitCommandError):
        gitops.remote_has_main(dest)


def test_push_publishes_frozen_main_commit_after_main_and_head_change(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    dest = tmp_path / "local"
    remote = git.Repo.init(tmp_path / "remote.git", bare=True)
    gitops.init(dest)
    repo = git.Repo(dest)
    repo.create_remote("origin", str(remote.git_dir))
    (dest / "input.txt").write_text("validated input")
    gitops.commit_all(dest)
    publication = gitops.main_commit(dest)
    assert gitops.commit_files(publication, ("input.txt",)) == {"input.txt": b"validated input"}

    (dest / "input.txt").write_text("later input")
    gitops.commit_all(dest, "advance local main after validation")
    repo.git.checkout("-b", "feature")
    assert repo.head.commit.hexsha != publication.hexsha
    assert gitops.main_commit(dest).hexsha == repo.heads.main.commit.hexsha
    gitops.push_main(dest, publication.hexsha)

    assert remote.heads.main.commit.hexsha == publication.hexsha
    assert remote.heads.main.commit.tree["input.txt"].data_stream.read() == b"validated input"
    assert repo.heads.main.tracking_branch().path == "refs/remotes/origin/main"


def test_main_commit_does_not_fall_back_to_a_different_head(tmp_path):
    repo = git.Repo.init(tmp_path, initial_branch="feature")
    (tmp_path / "input.txt").write_text("feature only")
    gitops.commit_all(tmp_path)
    assert repo.head.is_valid()
    with pytest.raises(RuntimeError, match="refs/heads/main"):
        gitops.main_commit(tmp_path)


def test_commit_files_rejects_symlink_blobs(tmp_path):
    gitops.init(tmp_path)
    (tmp_path / "actual.txt").write_text("fixture input")
    (tmp_path / "input.txt").symlink_to("actual.txt")
    gitops.commit_all(tmp_path)
    with pytest.raises(ValueError, match="regular file"):
        gitops.commit_files(gitops.main_commit(tmp_path), ("input.txt",))


@pytest.mark.parametrize("mutable_ref", ["main", "HEAD", "refs/heads/main"])
def test_push_requires_a_commit_id(tmp_path, mutable_ref):
    with pytest.raises(ValueError, match="immutable commit ID"):
        gitops.push_main(tmp_path, mutable_ref)
