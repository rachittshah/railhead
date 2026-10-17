"""Player economics from an action log alone: capital spent, routes, loan path, activity and errors."""

import json

from railhead.econ import analyse, ottd_date


def _log(tmp_path, rows):
    p = tmp_path / "actions.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


def test_ottd_date_matches_the_engine_calendar():
    assert ottd_date(715912) == "1960-02-07"


def test_analyse_counts_capital_routes_loan_and_behaviour(tmp_path):
    W = 512  # map width: tile = y * W + x
    rows = [
        {"kind": "header", "ticks": 74, "checksum": {"date": 715906, "ticks": 74}},
        {"kind": "act", "m": "company_loan", "ok": True, "a": {"amount": 300000}, "ret": {"cost": 0, "loan": 300000}, "ticks": 74},
        {"kind": "act", "m": "air_airport", "ok": True, "a": {"tile": 10 * W + 10, "type": "large"}, "ret": {"cost": 20000, "station": 0}, "ticks": 74},
        {"kind": "act", "m": "air_airport", "ok": True, "a": {"tile": 10 * W + 400, "type": "large"}, "ret": {"cost": 20000, "station": 1}, "ticks": 74},
        {"kind": "act", "m": "air_airport", "ok": True, "a": {"tile": 5, "type": "large", "dry_run": True}, "ret": {"cost": 20000}, "ticks": 74},
        {"kind": "act", "m": "vehicle_buy", "ok": True, "a": {"engine": 220}, "ret": {"cost": 37000, "engine": 220, "vehicle": 7}, "ticks": 74},
        {"kind": "act", "m": "order_append", "ok": True, "a": {"vehicle": 7, "tile": 10 * W + 10}, "ret": {"cost": 0}, "ticks": 74},
        {"kind": "act", "m": "order_append", "ok": True, "a": {"vehicle": 7, "tile": 10 * W + 400}, "ret": {"cost": 0}, "ticks": 74},
        {"kind": "act", "m": "vehicle_clone", "ok": True, "a": {"vehicle": 7, "share_orders": True}, "ret": {"cost": 37000, "vehicle": 8}, "ticks": 74},
        {"kind": "act", "m": "vehicle_buy", "ok": False, "a": {"engine": 220}, "code": "ERR_NOT_ENOUGH_CASH", "ticks": 74},
        {"kind": "wait", "a": {"days": 30}, "ticks": 74, "end_ticks": 74 + 30 * 74,
         "snap": {"date": "1960-03-02", "money": 200000, "loan": 300000, "net_worth": -100000, "income_q": 0, "vehicles": 2}},
        {"kind": "act", "m": "company_loan", "ok": True, "a": {"amount": 0}, "ret": {"cost": 0, "loan": 0}, "ticks": 74 + 30 * 74},
        {"kind": "wait", "a": {"days": 60}, "ticks": 74 + 30 * 74, "end_ticks": 74 + 90 * 74,
         "snap": {"date": "1960-05-01", "money": 50000, "loan": 0, "net_worth": 50000, "income_q": 90000, "vehicles": 2}},
    ]
    e = analyse(_log(tmp_path, rows), map_x=W)

    assert e["acts"] == 10 and e["failed"] == 1 and e["dry_runs"] == 1
    assert e["errors"] == {"ERR_NOT_ENOUGH_CASH": 1}
    assert e["capex"] == {"air": 40000, "vehicles": 74000}  # the dry run costs nothing
    assert e["vehicles_bought"]["air"] == 2
    assert e["legs"]["air"] == {"vehicle_legs": 2, "mean_tiles": 390, "max_tiles": 390}  # the clone shares the route
    assert e["loan"] == {"max": 300000, "first_max_date": "1960-03-02", "repaid_date": "1960-05-01"}
    assert e["first_income_date"] == "1960-05-01" and e["net_worth_positive_date"] == "1960-05-01"
    assert e["waits"] == {"count": 2, "median_days": 45.0, "total_days": 90}
    assert e["first_build"]["air"] == "1960-02-01"
    assert e["loan_events"] == [["1960-02-01", 300000], ["1960-03-02", 0]]  # every loan change, on its own date
    assert e["peak_vehicles"] == {"vehicles": 2, "date": "1960-03-02"}
