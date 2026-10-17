"""Engine start survives a port race: retry on fresh ports, and only return once our own admin port answers."""

from pathlib import Path

import pytest

from railhead import server as srv


class _FakeProc:
    def poll(self):
        return None


def _server(tmp_path, monkeypatch, outcomes):
    s = srv.OpenTTDServer(tmp_path, srv.ServerConfig(), binary=Path("/bin/true"), ready_timeout=1.0)
    ports = iter(range(41000, 41100))
    monkeypatch.setattr(srv, "free_port", lambda: next(ports))
    monkeypatch.setattr(s, "_write_files", lambda: None)
    monkeypatch.setattr(s, "_launch", lambda: setattr(s, "proc", _FakeProc()))
    monkeypatch.setattr(s, "stop", lambda timeout=5.0: None)
    calls = iter(outcomes)

    def check():
        r = next(calls)
        if isinstance(r, Exception):
            raise r

    monkeypatch.setattr(s, "_wait_ready", check)
    monkeypatch.setattr(s, "_confirm_admin", lambda: None)
    return s


def test_start_retries_on_fresh_ports_after_a_refused_admin_join(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch, [ConnectionError("admin join refused"), None])
    s.start()
    assert (s.admin_port, s.game_port) == (41002, 41003)  # the second attempt, on new ports


def test_start_gives_up_after_three_attempts(tmp_path, monkeypatch):
    s = _server(tmp_path, monkeypatch, [TimeoutError("not ready")] * 3)
    with pytest.raises(RuntimeError, match="3 attempts"):
        s.start()
