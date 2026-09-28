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
