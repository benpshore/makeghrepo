"""Rendered output must match tests/golden/*.golden exactly (see golden_snapshots.py)."""

import golden_snapshots as gs
import pytest

from makeghrepo import scaffold


def test_every_language_token_has_its_own_snapshot():
    assert set(scaffold.LANGUAGES) <= set(gs.COMBOS)
    assert gs.COMBOS["all"]["languages"] == list(scaffold.LANGUAGES)


@pytest.mark.parametrize("name", sorted(gs.COMBOS))
def test_rendered_output_matches_golden(tmp_path, name):
    pytest.skip("impl pending: #81")


def test_serialize_detects_a_one_byte_change(tmp_path):
    pytest.skip("impl pending: #81")


def test_serialize_detects_an_exec_bit_change(tmp_path):
    pytest.skip("impl pending: #81")


def test_no_stale_golden_files():
    pytest.skip("impl pending: #81")
