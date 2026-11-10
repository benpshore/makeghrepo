"""Persistent creation recovery with inert GitHub and actual local files."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from makeghrepo import github, project_state


@pytest.fixture
def projects(tmp_path, monkeypatch):
    path = tmp_path / "project.json"
    with project_state.ProjectState(path, "me/r", False) as fresh:
        fresh.initialize()
    state = {
        "path": path, "boards": {}, "visible": False, "creates": 0,
        "links": [], "searches": 0, "reads": [], "failure": None,
    }  # fmt: skip

    def fake(*args, body=None):
        if args[:2] == ("api", "graphql"):
            kind = "organization" if "organization(login:" in " ".join(args) else "user"
            if "projectsV2(" in " ".join(args):
                state["searches"] += 1
                boards = list(state["boards"].values()) if state["visible"] else []
                return json.dumps({"data": {kind: {"projectsV2": {"nodes": boards}}}})
            number = int(
                next(arg.removeprefix("number=") for arg in args if arg.startswith("number="))
            )
            state["reads"].append(number)
            return json.dumps({"data": {kind: {"projectV2": state["boards"].get(number)}}})
        if args[:2] == ("project", "create"):
            assert json.loads(path.read_text())["phase"] == "attempted"
            assert path.with_name(path.name + ".lock").exists()
            state["creates"] += 1
            number = state["creates"]
            state["boards"][number] = {"number": number, "title": "r"}
            if state.get("on_create"):
                state["on_create"]()
            if state["failure"] == "lost":
                raise github.GhError("lost response after successful remote create")
            if state["failure"] == "interrupt":
                raise KeyboardInterrupt
            if state["failure"] == "malformed":
                return "not json"
            if state["failure"] == "invalid-number":
                return '{"number": true}'
            return json.dumps({"number": number})
        if args[:2] == ("project", "link"):
            number = int(args[2])
            assert json.loads(path.read_text())["number"] == number
            if state["failure"] == "link":
                raise github.GhError("controlled link failure")
            state["links"].append(number)
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(github, "gh", fake)
    return state


def configure(state):
    return github.configure_project("me/r", state_path=state["path"])


@pytest.mark.parametrize("failure", ["lost", "malformed", "invalid-number", "interrupt"])
def test_uncertain_create_and_stale_empty_search_never_create_again(projects, failure):
    projects["failure"] = failure
    error = KeyboardInterrupt if failure == "interrupt" else github.GhError
    with pytest.raises(error):
        configure(projects)
    assert projects["creates"] == 1
    assert json.loads(projects["path"].read_text())["phase"] == "attempted"
    assert not projects["path"].with_suffix(".json.lock").exists()
    projects["failure"] = None
    for _ in range(2):
        with pytest.raises(github.GhError, match="remains uncertain"):
            configure(projects)
    assert projects["creates"] == 1 and projects["links"] == []
    projects["visible"] = True
    assert configure(projects) == 1
    assert projects["creates"] == 1 and projects["links"] == [1]


def test_known_identity_survives_link_failure_and_stale_search(projects):
    projects["failure"] = "link"
    with pytest.raises(github.GhError, match="link failure"):
        configure(projects)
    assert json.loads(projects["path"].read_text())["number"] == 1
    searches = projects["searches"]
    projects["failure"] = None
    assert configure(projects) == 1
    assert projects["searches"] == searches and projects["creates"] == 1


def test_failed_identity_save_preserves_uncertain_intent(projects, monkeypatch):
    original = project_state.ProjectState.save

    def fail_identity(self, number=None):
        if number is not None:
            raise OSError("controlled identity save failure")
        original(self, number)

    monkeypatch.setattr(project_state.ProjectState, "save", fail_identity)
    with pytest.raises(OSError, match="identity save"):
        configure(projects)
    assert json.loads(projects["path"].read_text())["phase"] == "attempted"
    monkeypatch.setattr(project_state.ProjectState, "save", original)
    with pytest.raises(github.GhError, match="remains uncertain"):
        configure(projects)
    assert projects["creates"] == 1
    projects["visible"] = True
    assert configure(projects) == 1


@pytest.mark.parametrize("failure", ["replace", "intent-directory-sync"])
def test_intent_persistence_failure_prevents_remote_creation(projects, monkeypatch, failure):
    if failure == "replace":

        def fail_replace(*args):
            raise OSError("controlled intent persistence failure")

        monkeypatch.setattr(project_state.os, "replace", fail_replace)
    else:
        original = project_state._sync_directory
        synced = []

        def fail_second_sync(path):
            synced.append(path)
            if len(synced) == 2:
                raise OSError("controlled intent persistence failure")
            original(path)

        monkeypatch.setattr(project_state, "_sync_directory", fail_second_sync)
    with pytest.raises(OSError, match="intent persistence failure"):
        configure(projects)
    assert projects["creates"] == 0 and projects["links"] == []


@pytest.mark.parametrize("change", ["missing", "renamed"])
def test_known_identity_never_falls_back_to_search_or_creation(projects, change):
    assert configure(projects) == 1
    searches = projects["searches"]
    if change == "missing":
        projects["boards"].clear()
    else:
        projects["boards"][1]["title"] = "renamed"
    with pytest.raises(github.GhError, match="Project identity"):
        configure(projects)
    assert projects["searches"] == searches and projects["creates"] == 1
    assert projects["links"] == [1]


@pytest.mark.parametrize("kind", ["corrupt", "scope", "org", "schema", "number", "symlink"])
def test_invalid_journal_blocks_even_remote_reads(projects, tmp_path, kind):
    data = {"schema": 1, "repo": "me/r", "org": False, "phase": "identified", "number": 1}
    if kind == "scope":
        data["repo"] = "other/r"
    elif kind == "org":
        data["org"] = True
    elif kind == "schema":
        data["schema"] = True
    elif kind == "number":
        data["number"] = True
    payload = "broken json" if kind == "corrupt" else json.dumps(data)
    if kind == "symlink":
        target = tmp_path / "other.json"
        target.write_text(payload)
        projects["path"].unlink()
        projects["path"].symlink_to(target)
    else:
        projects["path"].write_text(payload)
    with pytest.raises(project_state.ProjectStateError):
        configure(projects)
    assert projects["searches"] == 0 and projects["creates"] == 0 and projects["reads"] == []


def test_stale_lock_is_retained_and_not_assumed_safe(projects):
    lock = projects["path"].with_suffix(".json.lock")
    lock.write_text("inspect this interrupted invocation")
    with pytest.raises(project_state.ProjectStateError, match="locked"):
        configure(projects)
    assert lock.read_text() == "inspect this interrupted invocation"
    assert projects["searches"] == 0


def test_concurrent_local_invocation_cannot_dispatch_second_create(projects):
    entered, resume = threading.Event(), threading.Event()

    def pause_create():
        entered.set()
        assert resume.wait(5)

    projects["on_create"] = pause_create
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(configure, projects)
        try:
            assert entered.wait(5)
            with pytest.raises(project_state.ProjectStateError, match="locked"):
                configure(projects)
        finally:
            resume.set()
        assert first.result() == 1
    assert projects["creates"] == 1 and projects["links"] == [1]


def test_ambiguous_project_titles_never_choose_first(projects):
    projects["visible"] = True
    projects["boards"] = {1: {"number": 1, "title": "r"}, 2: {"number": 2, "title": "r"}}
    with pytest.raises(github.GhError, match="multiple exact-title"):
        configure(projects)
    assert projects["creates"] == 0 and projects["links"] == []


def test_missing_persistent_state_refuses_creation(projects):
    with pytest.raises(github.GhError, match="persistent local recovery"):
        github.configure_project("me/r")
    assert projects["creates"] == 0


def test_missing_or_deleted_journal_does_not_rearm_creation(projects):
    projects["path"].unlink()
    with pytest.raises(github.GhError, match="remains uncertain"):
        configure(projects)
    assert projects["creates"] == 0


def test_missing_legacy_journal_can_reuse_verified_existing_board(projects):
    projects["path"].unlink()
    projects["visible"] = True
    projects["boards"] = {7: {"number": 7, "title": "r"}}
    assert configure(projects) == 7
    assert projects["creates"] == 0 and projects["links"] == [7]


def test_initialize_never_resets_retained_intent(projects):
    with project_state.ProjectState(projects["path"], "me/r", False) as state:
        state.save()
    with (
        project_state.ProjectState(projects["path"], "me/r", False) as state,
        pytest.raises(project_state.ProjectStateError, match="Refusing to reset"),
    ):
        state.initialize()
    assert json.loads(projects["path"].read_text())["phase"] == "attempted"


def test_state_path_is_independent_of_process_cwd(projects, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert configure(projects) == 1
    assert list(elsewhere.iterdir()) == []


def test_git_directory_file_fails_before_project_effects(projects, tmp_path):
    dotgit = tmp_path / ".git"
    dotgit.write_text("gitdir: elsewhere")
    with pytest.raises(OSError):
        github.configure_project("me/r", state_path=dotgit / project_state.FILENAME)
    assert projects["searches"] == 0 and projects["creates"] == 0
