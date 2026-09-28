"""The CI `templates` matrix covers every combo, each on a runner that can run it."""

import json

import golden_snapshots as gs

PRIVATE = {"base-private", "python-private", "all-private"}


def test_every_public_combo_is_checked():
    assert {e["combo"] for e in gs.ci_matrix()["include"]} == set(gs.COMBOS) - PRIVATE


def test_runners():
    entries = gs.ci_matrix()["include"]
    assert {"combo": "objc", "os": gs.MAC_RUNNER} in entries
    assert {"combo": "c+cpp+objc+objcpp", "os": gs.MAC_RUNNER} in entries
    assert {"combo": "python", "os": gs.LINUX_RUNNER} in entries
    assert {"combo": "all", "os": gs.MAC_RUNNER} in entries
    assert {"combo": "all", "os": gs.LINUX_RUNNER} in entries
    # GitHub Free runs at most 5 macOS jobs at once.
    assert sum(e["os"] == gs.MAC_RUNNER for e in entries) <= 5


def test_matrix_round_trips_as_json():
    matrix = gs.ci_matrix()
    assert json.loads(json.dumps(matrix)) == matrix
