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
