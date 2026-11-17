"""Contract schemas (supply full, licence and loan stubbed) and the lifecycle transition table."""

import json

import pytest

from railhead.exchange.contracts import (
    ENABLED_KINDS,
    SCHEMAS,
    TRANSITIONS,
    Contract,
    schema_errors,
    validate_terms,
)
from railhead.exchange.errors import ExchangeError


def supply(**kw):
    t = {"seller": "a", "buyer": "b", "cargo": 1, "industry": 9, "quantity": 100, "window_start": 10,
         "deadline": 500, "price": 1000, "penalty_per_unit": 3}
    t.update(kw)
    return t


def test_schemas_are_json_documents_for_every_kind():
    assert set(SCHEMAS) == {"supply", "licence", "loan"}
    assert json.loads(json.dumps(SCHEMAS)) == SCHEMAS
    assert ENABLED_KINDS == {"supply"}


def test_valid_supply_normalises_bond_and_copies():
    t = supply()
    out = validate_terms("supply", t)
    assert out["bond"] == 0 and "bond" not in t
    assert validate_terms("supply", supply(bond=50))["bond"] == 50


@pytest.mark.parametrize("bad, fragment", [
    ({"quantity": 0}, "minimum"),
    ({"quantity": True}, "expected integer"),
    ({"price": -1}, "minimum"),
    ({"penalty_per_unit": 1.5}, "expected integer"),
    ({"bond": -5}, "minimum"),
    ({"cargo": "coal"}, "expected integer"),
    ({"seller": ""}, "shorter"),
    ({"extra": 1}, "unknown field 'extra'"),
    ({"deadline": 10}, "deadline must be after window_start"),
    ({"buyer": "a"}, "must be different"),
    ({"buyer": None}, "must be set"),
])
def test_bad_supply_terms_have_clear_errors(bad, fragment):
    with pytest.raises(ExchangeError) as e:
        validate_terms("supply", supply(**bad))
    assert e.value.code == "bad_terms" and fragment in e.value.message


def test_missing_field_and_non_object():
    t = supply()
    del t["deadline"]
    with pytest.raises(ExchangeError, match="missing required field 'deadline'"):
        validate_terms("supply", t)
    with pytest.raises(ExchangeError, match="expected object"):
        validate_terms("supply", [1])


def test_open_party_allows_exactly_one_null():
    assert validate_terms("supply", supply(buyer=None), open_party=True)["buyer"] is None
    with pytest.raises(ExchangeError, match="must be set"):
        validate_terms("supply", supply(buyer=None, seller=None), open_party=True)


def test_unknown_kind():
    with pytest.raises(ExchangeError) as e:
        validate_terms("swap", {})
    assert e.value.code == "unknown_kind"


def test_licence_and_loan_schemas_validate():
    lic = {"grantor": "a", "grantee": "b", "tiles": [1, 2], "term": 365, "annual_fee": 10}
    assert validate_terms("licence", lic) == lic
    with pytest.raises(ExchangeError, match="unique"):
        validate_terms("licence", {**lic, "tiles": [1, 1]})
    with pytest.raises(ExchangeError, match="fewer than 1"):
        validate_terms("licence", {**lic, "tiles": []})
    loan = {"lender": "a", "borrower": "b", "principal": 500, "interest_bp": 300, "due_tick": 900,
            "collateral_share_bp": 2500}
    assert validate_terms("loan", loan) == loan
    with pytest.raises(ExchangeError, match="maximum 10000"):
        validate_terms("loan", {**loan, "collateral_share_bp": 10001})


def test_schema_errors_reports_nested_paths():
    schema = {"type": "object", "properties": {"tiles": SCHEMAS["licence"]["properties"]["tiles"]}}
    errs = schema_errors({"tiles": [1, "x"]}, schema)
    assert errs == ["terms.tiles[1]: expected integer, got str"]


def _contract():
    return Contract(id="C1", kind="supply", terms=supply(), offerer="a", to="b", created_tick=0, expires_tick=99,
                    proposer="a", responder="b")


def test_transition_table_is_closed_and_terminal_states_are_final():
    for src, dsts in TRANSITIONS.items():
        assert dsts <= set(TRANSITIONS), src
    for terminal in ("settled", "expired", "withdrawn"):
        assert TRANSITIONS[terminal] == frozenset()


def test_contract_move_enforces_the_table_and_logs_events():
    c = _contract()
    c.move("signed", 5, "accept", "b")
    c.move("active", 10, "activate")
    with pytest.raises(ExchangeError) as e:
        c.move("withdrawn", 11, "withdraw", "a")
    assert e.value.code == "bad_transition"
    c.move("settled", 500, "settle")
    assert [ev["event"] for ev in c.events] == ["accept", "activate", "settle"]


def test_turn_taking():
    c = _contract()
    assert not c.can_respond("a") and c.can_respond("b") and not c.can_respond("z")
    c.responder = None
    assert c.can_respond("z") and not c.can_respond("a")
    assert c.is_party("a") and c.is_party("b") and not c.is_party("z")
