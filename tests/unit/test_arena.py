"""`railhead arena` and `railhead arena-run` argument handling (no engine)."""


def test_arena_cli_without_a_directory_prints_usage_not_a_traceback(capsys):
    from railhead import arena, arena_run

    assert arena.main([]) == 2 and "usage: railhead arena" in capsys.readouterr().err
    assert arena_run.main([]) == 2 and "usage: railhead arena-run" in capsys.readouterr().err
