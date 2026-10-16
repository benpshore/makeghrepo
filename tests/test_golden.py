"""Rendered output must match tests/golden/*.golden exactly (see golden_snapshots.py)."""

import base64

import golden_snapshots as gs
import pytest

from makeghrepo import scaffold


def test_every_language_token_has_its_own_snapshot():
    assert set(scaffold.LANGUAGES) <= set(gs.COMBOS)
    assert gs.COMBOS["all"]["languages"] == list(scaffold.LANGUAGES)


@pytest.mark.parametrize("name", sorted(gs.COMBOS))
def test_rendered_output_matches_golden(tmp_path, name):
    if not gs.golden_path(name).exists():
        pytest.fail(f"missing tests/golden/{name}.golden; run `uv run scripts/regen-golden`")
    actual = gs.serialize(gs.render_combo(name, tmp_path / "quiet-otter"))
    expected = gs.read_golden(name)
    if actual != expected:
        pytest.fail(gs.diff_report(name, expected, actual))


def _tree(root, content=b"hello\n", executable=False):
    root.mkdir()
    (root / "sub").mkdir()
    target = root / "sub" / "a.txt"
    target.write_bytes(content)
    target.chmod(0o755 if executable else 0o644)
    return root


def test_serialize_detects_a_one_byte_change(tmp_path):
    before = gs.serialize(_tree(tmp_path / "a", b"hello\n"))
    after = gs.serialize(_tree(tmp_path / "b", b"hellp\n"))
    assert before != after


def test_serialize_detects_a_trailing_newline_change(tmp_path):
    before = gs.serialize(_tree(tmp_path / "a", b"hello\n"))
    after = gs.serialize(_tree(tmp_path / "b", b"hello"))
    assert before != after
    assert "bytes=6" in before
    assert "bytes=5" in after


def test_serialize_detects_an_exec_bit_change(tmp_path):
    before = gs.serialize(_tree(tmp_path / "a"))
    after = gs.serialize(_tree(tmp_path / "b", executable=True))
    assert before != after
    assert "exec=0" in before
    assert "exec=1" in after


def test_serialize_format_dotfiles_and_binary(tmp_path):
    binary = b"\xff\x00binary"
    root = _tree(tmp_path / "t", binary)
    (root / ".hidden").write_bytes(b"dot\n")
    (root / ".hidden").chmod(0o644)
    encoded = base64.b64encode(binary).decode("ascii")
    assert gs.serialize(root) == (
        "### FILE .hidden exec=0 bytes=4 encoding=utf-8\ndot\n\n### END\n"
        f"### FILE sub/a.txt exec=0 bytes=8 encoding=base64\n{encoded}\n### END\n"
    )


def test_serialize_refuses_symlinks(tmp_path):
    root = _tree(tmp_path / "t")
    (root / "link").symlink_to(root / "sub" / "a.txt")
    with pytest.raises(ValueError, match="symlink"):
        gs.serialize(root)


def test_no_stale_golden_files():
    entries = list(gs.GOLDEN_DIR.iterdir())
    assert {p.name for p in entries} == {f"{name}.golden" for name in gs.COMBOS}
    assert all(p.is_file() and not p.is_symlink() for p in entries)


@pytest.fixture
def regen(monkeypatch):
    import runpy
    from pathlib import Path

    module = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/regen-golden"))
    monkeypatch.setattr(gs, "COMBOS", {"sample": {}})
    monkeypatch.setattr(gs, "render_combo", lambda *_: None)
    monkeypatch.setattr(gs, "serialize", lambda *_: "snapshot\n")

    def invoke(out, check=False):
        import sys

        monkeypatch.setattr(
            sys, "argv", ["regen-golden", "--out", str(out), *(["--check"] if check else [])]
        )
        return module["main"]()

    return invoke


@pytest.mark.parametrize(
    "kind",
    ["directory", "file", "symlink", "broken-symlink", "child-symlink", "nested", "stale-golden"],
)
def test_regen_refuses_unsafe_destinations_without_deleting_anything(tmp_path, regen, kind):
    out = tmp_path / "out"
    target = tmp_path / "target"
    target.mkdir()
    sentinel = target / "valuable.txt"
    sentinel.write_bytes(b"keep me")
    if kind == "file":
        out.write_bytes(b"keep me")
    elif kind in {"symlink", "broken-symlink"}:
        out.symlink_to(target if kind == "symlink" else tmp_path / "missing")
    else:
        out.mkdir()
        (out / "sample.golden").write_bytes(b"old snapshot")
        if kind == "directory":
            (out / "valuable.txt").write_bytes(b"keep me")
        elif kind == "nested":
            (out / "sample.golden").unlink()
            (out / "sample.golden").mkdir()
            (out / "sample.golden" / "valuable.txt").write_bytes(b"keep me")
        elif kind == "child-symlink":
            (out / "sample.golden").unlink()
            (out / "sample.golden").symlink_to(sentinel)
        else:
            (out / "obsolete.golden").write_bytes(b"keep me")
    import os

    before = os.lstat(out)
    assert regen(out) == 1
    assert os.lstat(out) == before
    assert sentinel.read_bytes() == b"keep me"
    if kind == "file":
        assert out.read_bytes() == b"keep me"
    elif kind == "directory":
        assert (out / "valuable.txt").read_bytes() == b"keep me"
        assert (out / "sample.golden").read_bytes() == b"old snapshot"
    elif kind == "nested":
        assert (out / "sample.golden" / "valuable.txt").read_bytes() == b"keep me"
    elif kind == "stale-golden":
        assert (out / "obsolete.golden").read_bytes() == b"keep me"


@pytest.mark.parametrize("existing", [False, True])
def test_regen_supports_dedicated_directories_and_read_only_check(tmp_path, regen, existing):
    out = tmp_path / "parent" / "out"
    if existing:
        out.mkdir(parents=True)
    assert regen(out) == 0
    assert (out / "sample.golden").read_bytes() == b"snapshot\n"
    assert regen(out) == 0
    assert regen(out, check=True) == 0
    (out / "sample.golden").write_bytes(b"wrong")
    assert regen(out, check=True) == 1
    assert (out / "sample.golden").read_bytes() == b"wrong"
    assert regen(out) == 0


@pytest.mark.parametrize("validation_status", [0, 1])
def test_documented_install_stops_on_failed_validation(tmp_path, validation_status):
    import os
    import subprocess
    from pathlib import Path

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    block = readme.split("staging=$(mktemp -d)", 1)[1].split("```", 1)[0]
    # Execute the documented commands with only external download/validation
    # tools stubbed. Real rm/mv exercise whether replacement is gated.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, script in {
        "gh": '#!/bin/sh\nprintf "new" > "$7/sample.golden"\n',
        "uv": f"#!/bin/sh\nexit {validation_status}\n",
        "git": "#!/bin/sh\nexit 0\n",
    }.items():
        executable = bin_dir / name
        executable.write_text(script)
        executable.chmod(0o755)
    golden = tmp_path / "tests" / "golden"
    golden.mkdir(parents=True)
    (golden / "sample.golden").write_bytes(b"original")
    staging = tmp_path / "staging"
    staging.mkdir()
    # Replace the interactive placeholder with a concrete run ID.
    command = f'staging="{staging}"' + block.replace("<trusted-run-id>", "123")
    result = subprocess.run(
        ["sh", "-c", command],
        cwd=tmp_path,
        env=os.environ | {"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
    )
    assert result.returncode == validation_status, result.stderr
    assert (golden / "sample.golden").read_bytes() == (
        b"new" if validation_status == 0 else b"original"
    )
    assert staging.exists() is (validation_status != 0)
