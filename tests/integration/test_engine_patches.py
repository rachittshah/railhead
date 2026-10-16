"""Railhead's engine patches (engine/patches/*.patch) are present and behave as specified."""

import time

import pytest

from railhead.bridge import Bridge
from railhead.server import OpenTTDServer, ServerConfig

pytestmark = pytest.mark.integration


@pytest.fixture
def world(tmp_path):
    with OpenTTDServer(tmp_path / "inst", ServerConfig(seed=11)) as srv:
        with srv.admin() as admin:
            bridge = Bridge(admin)
            bridge.wait_ready(timeout=30)
            yield srv, admin, bridge


def test_gamespeed_console_command_exists(world):
    _, admin, _ = world
    assert admin.rcon("gamespeed 0") == ["Game speed: 0"]
    assert admin.rcon("gamespeed") == ["Game speed: 0"]


def test_unlimited_speed_simulates_a_year_in_seconds(world):
    _, admin, bridge = world
    admin.rcon("gamespeed 0")
    t = time.monotonic()
    out = bridge.call("step", days=365, timeout=120)
    assert time.monotonic() - t < 15.0
    assert out["paused"] is True


def test_gamescript_can_send_messages_larger_than_stock_mtu(world):
    _, _, bridge = world
    assert bridge.call("echo", n=20_000) == "x" * 20_000


def test_oversize_gamescript_message_is_refused_not_crashing(world):
    _, _, bridge = world
    from railhead.bridge import BridgeError

    with pytest.raises(BridgeError) as ei:
        bridge.call("echo", n=40_000)
    assert ei.value.code == "ERR_SEND_FAILED"
    assert bridge.call("ping") == "pong"  # the script survived


def test_reseed_console_command_exists(world):
    _, admin, _ = world
    assert admin.rcon("reseed 12345") == ["Reseeded: 12345"]
