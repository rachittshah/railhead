"""Real OpenTTD dedicated server + Admin Port client (needs a built OpenTTD; see engine/README)."""

from pathlib import Path

import pytest

from railhead.admin_client import AdminClient
from railhead.server import OpenTTDServer, ServerConfig

pytestmark = pytest.mark.integration


def test_server_boots_with_requested_seed_and_map_size(tmp_path):
    cfg = ServerConfig(seed=4242, map_x_log2=6, map_y_log2=7, game_script=None)
    with OpenTTDServer(tmp_path / "inst", cfg) as srv:
        with AdminClient.connect("127.0.0.1", srv.admin_port, srv.admin_password) as admin:
            assert admin.welcome.seed == 4242
            assert (admin.welcome.map_x, admin.welcome.map_y) == (64, 128)
            assert admin.welcome.dedicated is True


def test_rcon_returns_console_output_lines(tmp_path):
    with OpenTTDServer(tmp_path / "inst", ServerConfig(seed=1, game_script=None)) as srv:
        with AdminClient.connect("127.0.0.1", srv.admin_port, srv.admin_password) as admin:
            assert admin.rcon("echo railhead-online") == ["railhead-online"]


def test_wrong_password_is_rejected(tmp_path):
    with OpenTTDServer(tmp_path / "inst", ServerConfig(seed=1, game_script=None)) as srv:
        with pytest.raises(ConnectionError):
            AdminClient.connect("127.0.0.1", srv.admin_port, "not-the-password")


def test_two_servers_run_side_by_side_on_distinct_ports(tmp_path):
    with OpenTTDServer(tmp_path / "a", ServerConfig(seed=1, game_script=None)) as a, OpenTTDServer(
        tmp_path / "b", ServerConfig(seed=2, game_script=None)
    ) as b:
        assert a.admin_port != b.admin_port
        with AdminClient.connect("127.0.0.1", a.admin_port, a.admin_password) as ca, AdminClient.connect(
            "127.0.0.1", b.admin_port, b.admin_password
        ) as cb:
            assert (ca.welcome.seed, cb.welcome.seed) == (1, 2)


def test_server_works_with_a_relative_workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with OpenTTDServer(Path("rel/inst"), ServerConfig(seed=1, game_script=None)) as srv:
        with AdminClient.connect("127.0.0.1", srv.admin_port, srv.admin_password) as admin:
            assert admin.welcome.seed == 1
