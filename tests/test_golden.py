"""Golden snapshots of rendered output. Tests that byte-exact rendering is deterministic."""

from pathlib import Path

import pytest

from makeghrepo import scaffold

# Snapshot combo list: all existing COMBOS plus single-token variants and special cases.
# DO NOT import COMBOS from test_template.py; F2 will rewrite that list, and the golden
# set must stay stable. This list is copied from the plan at F1 acceptance.
# Keep this in sync by hand: single token per LANGUAGES entry, the existing COMBOS,
# plus private=True and py_lib=True variants.
# TODO(opus): confirm which base combo renders what private gates (codeql, etc.)
COMBOS = [
    [],
    ["python"],
    ["rust"],
    ["swift"],
    ["js"],
    ["css"],
    ["c"],
    ["cpp"],
    ["objc"],
    ["objcpp"],
    ["api"],
    ["postgres"],
    ["sql"],
    ["docker"],
    ["shell"],
    # Existing multi-token combos (from test_template.py, copied verbatim)
    ["rust", "docker"],
    ["swift", "python"],
    ["js", "css", "api"],
    ["c", "cpp", "objc", "objcpp"],
    ["postgres", "sql", "shell"],
    list(scaffold.LANGUAGES),
    # Special variants
    # TODO(opus): pick a base combo for private=True that renders codeql/actions-related features
    # (e.g., something with enough language diversity to test the visibility logic).
]


def combo_id(combo: list[str], **kwargs: object) -> str:
    """Stable ID for a golden snapshot directory."""
    parts = ["+".join(combo) or "none"]
    if kwargs.get("private"):
        parts.append("private")
    if kwargs.get("py_lib"):
        parts.append("py_lib")
    return "-".join(parts)


def render_golden(tmp_path: Path, combo: list[str], **kwargs: object) -> Path:
    """Render a project to a temp directory.

    TODO(opus): reuse the render() helper from test_template.py.
    TODO(opus): check whether vcs_ref is needed (uncommitted template edits, etc.).
    """
    dest = tmp_path / "golden-render"
    data = {
        "project_name": "golden-render",
        "package_name": "golden_render",
        "description": "golden snapshot render",
        "author_name": "Golden Snapshot",
        "github_owner": "test",
        "languages": combo,
    }
    # TODO(opus): fix year to a constant (e.g., 2026) to avoid snapshot drift.
    scaffold.render(dest, {**data, **kwargs})
    return dest


@pytest.mark.parametrize("combo", COMBOS, ids=lambda c: combo_id(c))
def test_golden_matches(tmp_path: Path, combo: list[str]) -> None:
    """Rendered output matches the golden snapshot byte-for-byte.

    TODO(opus): implement byte comparison against tests/golden/<id>/.
    """
    pytest.skip("impl pending: #81")


@pytest.mark.parametrize("combo", COMBOS, ids=lambda c: combo_id(c))
def test_golden_file_modes(tmp_path: Path, combo: list[str]) -> None:
    """Rendered output has the correct file modes (especially exec bits).

    TODO(opus): implement file-mode manifest (see design doc for format).
    """
    pytest.skip("impl pending: #81")


def test_one_byte_change_detected(tmp_path: Path) -> None:
    """Test that a single byte change in a template is detected.

    This is the gate: if this test passes, golden snapshots catch template regressions.
    TODO(opus): break one template (e.g., change a string), render, and confirm mismatch.
    """
    pytest.skip("impl pending: #81")


def test_golden_no_collection_errors() -> None:
    """Ensure the combo list is valid and doesn't break pytest collection.

    This runs without tmp_path and catches errors early.
    """
    # TODO(opus): verify that all combos in COMBOS are valid language lists,
    # and that the ids() function doesn't produce duplicates.
    assert len(COMBOS) > 0


def test_golden_regen_script_exists() -> None:
    """Verify that scripts/regen-golden exists and is executable."""
    # TODO(opus): check that Path(__file__).parent.parent / "scripts" / "regen-golden"
    # exists and is executable. For now, this test is skipped to allow skeleton to pass pytest.
    pytest.skip("impl pending: #81")


# TODO(opus): should this test be marked "slow"? Rendering all combos is expensive.
# For skeleton, all tests are skipped anyway. Decide during implementation.

# TODO(opus): rendered tests/test_*.py and pyproject.toml files under tests/golden/<id>/
# will be collected by pytest and linted by ruff. Ensure either:
# (a) they are moved outside the tests/ directory, or
# (b) they are named to avoid pytest collection (e.g., _*.py), or
# (c) they are registered in pytest config to be ignored.
