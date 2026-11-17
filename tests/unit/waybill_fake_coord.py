"""A Waybill coordinator on the fake engine of test_multi_coordinator, as a process (for the arena runner
tests): python -m tests.unit.waybill_fake_coord <task_dir> <state_dir> [--turn-s S]"""

from __future__ import annotations

import signal
import sys
from pathlib import Path

from tests.unit.test_multi_coordinator import FakeCoordinator


def main(argv: list[str]) -> int:
    turn = None
    if "--turn-s" in argv:
        i = argv.index("--turn-s")
        turn, argv = float(argv[i + 1]), argv[:i] + argv[i + 2:]
    c = FakeCoordinator(Path(argv[0]), Path(argv[1]).resolve(), turn_budget_s=turn).start()
    signal.signal(signal.SIGTERM, lambda *_: c.stop.set())
    while not c.stop.wait(0.2):
        pass
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
