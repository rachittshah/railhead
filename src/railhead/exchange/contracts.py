"""Contract types, their JSON schemas, and the contract lifecycle state machine.

``SCHEMAS`` holds one JSON Schema (draft 2020-12 subset) per contract kind. :func:`validate_terms` checks a
terms dict against it with a small built-in validator (the project has no runtime dependencies), then
applies the cross-field rules that JSON Schema cannot express.

Only ``supply`` is enabled for trading. ``licence`` and ``loan`` have schemas and validate, but
:data:`ENABLED_KINDS` excludes them until their settlement rules exist.

Lifecycle::

    offered -> countered (repeatable) -> signed -> active -> settled (kept | partial | broken)
    offered/countered -> expired | withdrawn
    signed/active: amendment (both parties sign identical terms before the deadline)
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .errors import ExchangeError
from .ledger import is_int

_INT0 = {"type": "integer", "minimum": 0}
_PARTY = {"type": "string", "minLength": 1}
_OPEN_PARTY = {"type": ["string", "null"], "minLength": 1}

SCHEMAS: dict[str, dict[str, Any]] = {
    "supply": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "supply",
        "type": "object",
        "additionalProperties": False,
        "required": ["seller", "buyer", "cargo", "industry", "quantity", "window_start", "deadline", "price",
                     "penalty_per_unit"],
        "properties": {
            "seller": _OPEN_PARTY, "buyer": _OPEN_PARTY,
            "cargo": _INT0, "industry": _INT0,
            "quantity": {"type": "integer", "minimum": 1},
            "window_start": _INT0, "deadline": _INT0,
            "price": _INT0, "penalty_per_unit": _INT0, "bond": _INT0,
        },
    },
    "licence": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "licence",
        "type": "object",
        "additionalProperties": False,
        "required": ["grantor", "grantee", "tiles", "term", "annual_fee"],
        "properties": {
            "grantor": _OPEN_PARTY, "grantee": _OPEN_PARTY,
            "tiles": {"type": "array", "minItems": 1, "items": _INT0},
            "term": {"type": "integer", "minimum": 1},
            "annual_fee": _INT0,
        },
    },
    "loan": {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "loan",
        "type": "object",
        "additionalProperties": False,
        "required": ["lender", "borrower", "principal", "interest_bp", "due_tick", "collateral_share_bp"],
        "properties": {
            "lender": _OPEN_PARTY, "borrower": _OPEN_PARTY,
            "principal": {"type": "integer", "minimum": 1},
            "interest_bp": _INT0,
            "due_tick": _INT0,
            "collateral_share_bp": {"type": "integer", "minimum": 0, "maximum": 10000},
        },
    },
}

PARTY_FIELDS: dict[str, tuple[str, str]] = {
    "supply": ("seller", "buyer"),
    "licence": ("grantor", "grantee"),
    "loan": ("lender", "borrower"),
}
ENABLED_KINDS = frozenset({"supply"})

# status -> statuses it may move to
TRANSITIONS: dict[str, frozenset[str]] = {
    "offered": frozenset({"countered", "signed", "expired", "withdrawn"}),
    "countered": frozenset({"countered", "signed", "expired", "withdrawn"}),
    "signed": frozenset({"active", "settled"}),
    "active": frozenset({"settled"}),
    "settled": frozenset(), "expired": frozenset(), "withdrawn": frozenset(),
}
OPEN_STATUSES = frozenset({"offered", "countered"})
LIVE_STATUSES = frozenset({"signed", "active"})
OUTCOMES = ("kept", "partial", "broken")


# ---------------------------------------------------------------------------------------------- validation
def _type_ok(value: Any, typ: str) -> bool:
    return {
        "integer": is_int(value),
        "string": isinstance(value, str),
        "null": value is None,
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }[typ]


def schema_errors(value: Any, schema: dict[str, Any], path: str = "terms") -> list[str]:
    """Errors from the JSON Schema subset used by :data:`SCHEMAS` (type, required, properties,
    additionalProperties, minimum, maximum, minLength, items, minItems)."""
    types = schema.get("type")
    if types is not None:
        types = [types] if isinstance(types, str) else types
        if not any(_type_ok(value, t) for t in types):
            return [f"{path}: expected {'|'.join(types)}, got {type(value).__name__}"]
    errs: list[str] = []
    if isinstance(value, dict):
        props = schema.get("properties", {})
        errs += [f"{path}: missing required field '{k}'" for k in schema.get("required", []) if k not in value]
        if schema.get("additionalProperties") is False:
            errs += [f"{path}: unknown field '{k}'" for k in sorted(set(value) - set(props))]
        for k in sorted(set(value) & set(props)):
            errs += schema_errors(value[k], props[k], f"{path}.{k}")
    if is_int(value):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: {value} > maximum {schema['maximum']}")
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        errs.append(f"{path}: shorter than {schema['minLength']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errs.append(f"{path}: fewer than {schema['minItems']} items")
        if "items" in schema:
            for i, item in enumerate(value):
                errs += schema_errors(item, schema["items"], f"{path}[{i}]")
    return errs


def validate_terms(kind: str, terms: Any, *, open_party: bool = False) -> dict[str, Any]:
    """Validate ``terms`` for ``kind`` and return a normalised deep copy (``bond`` defaults to 0).

    ``open_party`` allows exactly one party field to be null (a public offer to anyone). Raises
    :class:`ExchangeError` ``unknown_kind`` or ``bad_terms``."""
    if kind not in SCHEMAS:
        raise ExchangeError("unknown_kind", f"{kind!r} (kinds: {', '.join(sorted(SCHEMAS))})")
    errs = schema_errors(terms, SCHEMAS[kind])
    if errs:
        raise ExchangeError("bad_terms", "; ".join(errs))
    t = copy.deepcopy(terms)
    a, b = PARTY_FIELDS[kind]
    nulls = [p for p in (a, b) if t[p] is None]
    if nulls and not (open_party and len(nulls) == 1):
        raise ExchangeError("bad_terms", f"party field(s) {nulls} must be set")
    if t[a] is not None and t[a] == t[b]:
        raise ExchangeError("bad_terms", f"{a} and {b} must be different companies")
    if kind == "supply":
        t.setdefault("bond", 0)
        if t["deadline"] <= t["window_start"]:
            raise ExchangeError("bad_terms", "deadline must be after window_start")
    if kind == "licence" and len(set(t["tiles"])) != len(t["tiles"]):
        raise ExchangeError("bad_terms", "tiles must be unique")
    return t


def parties(kind: str, terms: dict[str, Any]) -> tuple[Any, Any]:
    a, b = PARTY_FIELDS[kind]
    return terms[a], terms[b]


# ---------------------------------------------------------------------------------------------- lifecycle
@dataclass
class Contract:
    id: str
    kind: str
    terms: dict[str, Any]
    offerer: str
    to: str | None                 # addressed counterparty, or None for a public order-book offer
    created_tick: int
    expires_tick: int
    proposer: str                  # who made the latest proposal (offer or counter)
    responder: str | None          # who may accept or counter next (None = anyone but the proposer)
    status: str = "offered"
    tender_id: str | None = None
    signed_tick: int | None = None
    terms_history: list[dict[str, Any]] = field(default_factory=list)   # [{from_tick, terms}] once signed
    pending_amendment: dict[str, Any] | None = None                     # {by, tick, terms}
    escrow_price: int = 0
    escrow_bond: int = 0
    outcome: str | None = None
    settlement: dict[str, Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def move(self, new_status: str, tick: int, event: str, company: str | None = None) -> None:
        if new_status not in TRANSITIONS[self.status]:
            raise ExchangeError("bad_transition", f"{self.id}: {self.status} -> {new_status} is not allowed")
        self.status = new_status
        self.events.append({"tick": tick, "event": event, "company": company})

    def is_party(self, company: str) -> bool:
        return company in parties(self.kind, self.terms) or company in (self.offerer, self.to)

    def can_respond(self, company: str) -> bool:
        if company == self.proposer:
            return False
        return self.responder is None or self.responder == company

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.__dict__)
