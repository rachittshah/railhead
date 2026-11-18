"""Off-world ledger in integer pounds.

Accounts: one per company (named by the company id), ``exchange`` (pays tenders, receives tender penalties;
may go negative without limit) and ``escrow:<contract_id>`` / ``escrow:<contract_id>:bond`` (hold money for a
signed contract; never negative). Every operation is one balanced transfer, so the sum of all balances is
always 0 (:meth:`Ledger.check_conservation`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .errors import ExchangeError

EXCHANGE = "exchange"
ESCROW_PREFIX = "escrow:"


def escrow_account(contract_id: str) -> str:
    return f"{ESCROW_PREFIX}{contract_id}"


def bond_account(contract_id: str) -> str:
    return f"{ESCROW_PREFIX}{contract_id}:bond"


def is_int(x: object) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def check_company_id(name: object) -> str:
    if not isinstance(name, str) or not name or ":" in name or name == EXCHANGE:
        raise ExchangeError("bad_company", f"company id must be a non-empty string without ':' and not "
                                           f"{EXCHANGE!r}: {name!r}")
    return name


@dataclass(frozen=True)
class Transfer:
    seq: int
    tick: int
    src: str
    dst: str
    amount: int
    reason: str
    ref: str


class Ledger:
    def __init__(self, companies: list[str], credit_limit: int | None = None):
        if credit_limit is not None and (not is_int(credit_limit) or credit_limit < 0):
            raise ExchangeError("bad_amount", "credit_limit must be a non-negative int or None")
        self.companies = tuple(check_company_id(c) for c in companies)
        if len(set(self.companies)) != len(self.companies):
            raise ExchangeError("bad_company", "duplicate company id")
        self.credit_limit = credit_limit
        self._balances: dict[str, int] = {c: 0 for c in self.companies}
        self._balances[EXCHANGE] = 0
        self.history: list[Transfer] = []

    # ------------------------------------------------------------------ queries
    def balance(self, account: str) -> int:
        return self._balances.get(account, 0)

    def balances(self) -> dict[str, int]:
        return {k: self._balances[k] for k in sorted(self._balances)}

    def _known(self, account: str) -> bool:
        return account in self.companies or account == EXCHANGE or account.startswith(ESCROW_PREFIX)

    def can_debit(self, account: str, amount: int) -> bool:
        """True when a voluntary (non-forced) debit of ``amount`` from ``account`` is allowed."""
        if account == EXCHANGE:
            return True
        if account.startswith(ESCROW_PREFIX):
            return self.balance(account) >= amount
        if self.credit_limit is None:
            return True
        return self.balance(account) - amount >= -self.credit_limit

    # ------------------------------------------------------------------ mutation
    def transfer(self, src: str, dst: str, amount: int, *, reason: str, ref: str = "", tick: int = 0,
                 force: bool = False) -> Transfer:
        """Move ``amount`` from ``src`` to ``dst``. ``force`` ignores the company credit limit (penalties),
        never the escrow floor."""
        if not is_int(amount) or amount <= 0:
            raise ExchangeError("bad_amount", f"amount must be a positive int: {amount!r}")
        for acct in (src, dst):
            if not isinstance(acct, str) or not self._known(acct):
                raise ExchangeError("unknown_account", repr(acct))
        if src == dst:
            raise ExchangeError("self_transfer", src)
        if src.startswith(ESCROW_PREFIX) and self.balance(src) < amount:
            raise ExchangeError("insufficient_funds", f"{src} holds {self.balance(src)} < {amount}")
        if not force and not self.can_debit(src, amount):
            raise ExchangeError("insufficient_funds", f"{src} balance {self.balance(src)} - {amount} is below "
                                                      f"the credit limit -{self.credit_limit}")
        t = Transfer(len(self.history), tick, src, dst, amount, reason, ref)
        self._balances[src] = self.balance(src) - amount
        self._balances[dst] = self.balance(dst) + amount
        for acct in (src, dst):  # closed escrow accounts disappear from the balance sheet
            if acct.startswith(ESCROW_PREFIX) and self._balances[acct] == 0:
                del self._balances[acct]
        self.history.append(t)
        total = sum(self._balances.values())  # cheap per-transfer check; check_conservation also replays history
        assert total == 0, f"ledger not conserved after {t}: sum = {total}"
        return t

    def pay(self, src: str, dst: str, amount: int, *, tick: int = 0, ref: str = "") -> Transfer:
        """Voluntary company-to-company transfer."""
        for acct in (src, dst):
            if acct not in self.companies:
                raise ExchangeError("unknown_account", f"pay is between companies only: {acct!r}")
        return self.transfer(src, dst, amount, reason="pay", ref=ref, tick=tick)

    # ------------------------------------------------------------------ audit
    def check_conservation(self) -> None:
        total = sum(self._balances.values())
        assert total == 0, f"ledger not conserved: sum of balances = {total}"
        replayed: dict[str, int] = {}
        for t in self.history:
            replayed[t.src] = replayed.get(t.src, 0) - t.amount
            replayed[t.dst] = replayed.get(t.dst, 0) + t.amount
        for acct in set(replayed) | set(self._balances):
            assert replayed.get(acct, 0) == self._balances.get(acct, 0), f"history disagrees for {acct}"

    def state(self) -> dict:
        return {"balances": self.balances(), "history": [asdict(t) for t in self.history]}
