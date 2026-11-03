from pathlib import Path

import gitcli
import pytest

from makeghrepo import gitops


def test_ensure_identity_lets_commit_succeed_without_any_host_identity(tmp_path, monkeypatch):
    """A fresh host/CI runner with no git identity configured anywhere must not block a commit."""
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.delenv(f"GIT_{role}_NAME", raising=False)
        monkeypatch.delenv(f"GIT_{role}_EMAIL", raising=False)
    # Without configured identity, git may still guess user@hostname (macOS
    # hostnames end in .local). Forbid guessing so "no identity" is real on every host.
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.useConfigOnly")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    gitops.init(tmp_path)
    (tmp_path / "f.txt").write_text("x")
    gitcli.git(tmp_path, "add", "--all")
    assert gitcli.git(tmp_path, "commit", "-m", "test", check=False).returncode != 0

    gitops.ensure_identity(tmp_path, "Ada Lovelace", "ada@users.noreply.github.com")
    gitcli.git(tmp_path, "commit", "-m", "test")
    assert gitcli.out(tmp_path, "log", "-1", "--format=%an|%ae") == (
        "Ada Lovelace|ada@users.noreply.github.com"
    )


def test_ensure_identity_never_overrides_a_real_one(tmp_path):
    gitops.init(tmp_path)
    gitcli.git(tmp_path, "config", "user.name", "Real Person")
    gitcli.git(tmp_path, "config", "user.email", "real@example.com")

    gitops.ensure_identity(tmp_path, "Fallback", "fallback@users.noreply.github.com")

    assert gitcli.out(tmp_path, "config", "user.name") == "Real Person"
    assert gitcli.out(tmp_path, "config", "user.email") == "real@example.com"


MARKER = {"schema": 1, "name": "x", "owner": "me", "private": True, "languages": ["python"]}


def test_marker_round_trips_and_stays_out_of_the_work_tree(tmp_path):
    gitops.init(tmp_path)
    gitops.write_marker(tmp_path, MARKER)
    assert gitops.read_marker(tmp_path) == MARKER
    assert (tmp_path / ".git" / "makeghrepo.json").is_file()
    assert gitcli.out(tmp_path, "status", "--porcelain") == ""


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
    gitcli.init(remote, "--bare")
    gitcli.git(dest, "remote", "add", "origin", str(remote))
    assert gitops.remote_has_main(dest) is False

    (dest / "ready.txt").write_text("ready")
    gitops.commit_all(dest)
    gitops.push_main(dest)
    assert gitops.remote_has_main(dest) is True

    gitcli.git(dest, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    with pytest.raises(gitops.GitError):
        gitops.remote_has_main(dest)

    gitcli.git(dest, "remote", "remove", "origin")
    with pytest.raises(gitops.GitError):
        gitops.remote_has_main(dest)


def test_missing_git_raises_a_clean_git_error(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))  # an empty directory: no git here
    with pytest.raises(gitops.GitError, match="could not run") as caught:
        gitops.init(tmp_path / "repo")
    assert caught.value.returncode is None


def test_failed_git_reports_exit_status_and_stderr(tmp_path):
    with pytest.raises(gitops.GitError, match=r"exited with \d+: .+") as caught:
        gitops.push_main(tmp_path)  # not a repo
    assert caught.value.returncode not in (None, 0)
    assert caught.value.stderr


def test_author_from_git_config_reads_global_and_tolerates_unset(tmp_path, monkeypatch):
    config = tmp_path / "gitconfig"
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    assert gitops.author_from_git_config() == ("", "")
    config.write_text("[user]\n\tname = Ada Lovelace\n\temail = ada@example.com\n")
    assert gitops.author_from_git_config() == ("Ada Lovelace", "ada@example.com")
