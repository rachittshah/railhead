"""Waybill ledger: integer pounds, balanced transfers only, conservation, audit history."""

import pytest

from railhead.exchange.errors import ExchangeError
from railhead.exchange.ledger import EXCHANGE, Ledger, escrow_account


def test_new_ledger_opens_company_accounts_at_zero():
    led = Ledger(["a", "b"])
    assert led.balance("a") == 0 and led.balance("b") == 0 and led.balance(EXCHANGE) == 0
    assert led.history == []
    led.check_conservation()


def test_pay_moves_money_and_records_history():
    led = Ledger(["a", "b"])
    led.pay("a", "b", 100, tick=5)
    assert led.balance("a") == -100 and led.balance("b") == 100
    (t,) = led.history
    assert (t.seq, t.src, t.dst, t.amount, t.reason, t.tick) == (0, "a", "b", 100, "pay", 5)
    led.check_conservation()


def test_credit_limit_blocks_voluntary_overdraft_but_not_forced_transfers():
    led = Ledger(["a", "b"], credit_limit=50)
    led.pay("a", "b", 50)
    with pytest.raises(ExchangeError) as e:
        led.pay("a", "b", 1)
    assert e.value.code == "insufficient_funds"
    led.transfer("a", "b", 30, reason="penalty", force=True)
    assert led.balance("a") == -80


def test_escrow_accounts_can_never_go_negative():
    led = Ledger(["a", "b"])
    with pytest.raises(ExchangeError) as e:
        led.transfer(escrow_account("C1"), "a", 1, reason="x", force=True)
    assert e.value.code == "insufficient_funds"


def test_exchange_account_is_an_unlimited_source():
    led = Ledger(["a"], credit_limit=0)
    led.transfer(EXCHANGE, "a", 1_000_000, reason="tender")
    assert led.balance(EXCHANGE) == -1_000_000


@pytest.mark.parametrize("amount", [0, -1, 1.5, True, "3"])
def test_amount_must_be_a_positive_int(amount):
    led = Ledger(["a", "b"])
    with pytest.raises(ExchangeError) as e:
        led.pay("a", "b", amount)
    assert e.value.code == "bad_amount"
    assert led.history == []


def test_unknown_accounts_and_self_transfers_are_rejected():
    led = Ledger(["a", "b"])
    for src, dst, code in [("a", "z", "unknown_account"), ("z", "a", "unknown_account"), ("a", "a", "self_transfer")]:
        with pytest.raises(ExchangeError) as e:
            led.pay(src, dst, 1)
        assert e.value.code == code
    with pytest.raises(ExchangeError) as e:
        led.pay(EXCHANGE, "a", 1)
    assert e.value.code == "unknown_account"  # pay is company -> company only


@pytest.mark.parametrize("name", ["exchange", "escrow:x", "a:b", "", 7])
def test_reserved_or_bad_company_names_are_rejected(name):
    with pytest.raises(ExchangeError) as e:
        Ledger([name])
    assert e.value.code == "bad_company"


def test_conservation_detects_tampering():
    led = Ledger(["a", "b"])
    led.pay("a", "b", 10)
    led._balances["a"] += 1
    with pytest.raises(AssertionError):
        led.check_conservation()


def test_state_round_trip_is_canonical():
    led = Ledger(["b", "a"])
    led.pay("a", "b", 7, tick=1, ref="memo")
    led.transfer("b", escrow_account("C1"), 3, reason="escrow", ref="C1", tick=2)
    s = led.state()
    assert s["balances"] == {"a": -7, "b": 4, "escrow:C1": 3, "exchange": 0}
    assert list(s["balances"]) == sorted(s["balances"])
    assert [h["seq"] for h in s["history"]] == [0, 1]
