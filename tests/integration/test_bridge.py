"""GameScript bridge: handshake, lockstep stepping, determinism of the paused-input protocol."""

import pytest

from railhead.bridge import Bridge
from railhead.server import OpenTTDServer, ServerConfig

pytestmark = pytest.mark.integration


@pytest.fixture
def world(tmp_path):
    with OpenTTDServer(tmp_path / "inst", ServerConfig(seed=7)) as srv:
        with srv.admin() as admin:
            bridge = Bridge(admin)
            bridge.wait_ready(timeout=30)
            yield srv, admin, bridge


def test_bridge_handshake_reports_protocol_version(world):
    _, _, bridge = world
    assert bridge.hello["v"] >= 1


def test_ping_round_trip(world):
    _, _, bridge = world
    assert bridge.call("ping") == "pong"


def test_world_is_frozen_until_stepped(world):
    _, _, bridge = world
    s0 = bridge.call("state")
    assert s0["paused"] is True
    # wall-clock passes, game time must not
    import time

    time.sleep(0.5)
    assert bridge.call("state")["ticks"] == s0["ticks"]


def test_step_advances_exactly_n_ticks_then_refreezes(world):
    _, _, bridge = world
    t0 = bridge.call("state")["ticks"]
    out = bridge.call("step", ticks=37, timeout=60)
    assert out["ticks"] == t0 + 37
    assert bridge.call("state")["paused"] is True


def test_seventy_four_ticks_make_one_day(world):
    _, _, bridge = world
    d0 = bridge.call("state")["date"]
    bridge.call("step", ticks=74 * 3, timeout=60)
    assert bridge.call("state")["date"] == d0 + 3


def test_step_days_advances_economy_date(world):
    _, _, bridge = world
    d0 = bridge.call("state")["date"]
    out = bridge.call("step", days=2, timeout=60)
    assert out["date"] == d0 + 2


def test_unknown_method_returns_structured_error(world):
    _, _, bridge = world
    from railhead.bridge import BridgeError

    with pytest.raises(BridgeError) as ei:
        bridge.call("no_such_method")
    assert ei.value.code == "ERR_UNKNOWN_METHOD"
