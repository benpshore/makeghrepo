import quiet_otter


def test_main_runs(capsys):
    quiet_otter.main()
    assert "quiet-otter" in capsys.readouterr().out
