# The Waybill exchange in Bend 2

The Waybill exchange (contracts, escrow, settlement, obligations; [WAYBILL-EXCHANGE.md](WAYBILL-EXCHANGE.md))
is written twice. The Python package `railhead.exchange` runs the games and is the reference. A second,
independent implementation of the same replay rules is written in [Bend 2](https://github.com/bendlang/bend)
(v2.0.40), a language whose compiler checks proofs of stated laws. Two things follow:

- **N-version agreement.** For any log, both implementations must give byte-identical output: the same final
  exchange state, or the same error code at the same record. `railhead waybill-bend verify` checks a log.
- **Machine-checked laws.** 19 laws about the Bend code, among them ledger conservation, are proven, and the
  proofs are checked a second time by Bend's proven kernel (`bend PROOF.bend --verdict`).

This is a port of the exchange **replay core** only. The rest of Railhead and Waybill (the coordinator,
the engine bridge, the world replay, the agents' command API, tender generation) stays in Python. The
pre-registered campaign is not affected: it runs the Python exchange at the locked commit, and the Bend core
is a cross-check run on its logs afterwards, not part of the analysis.

## 1. What is ported

| Python (`src/railhead/exchange/`) | Bend 2 (`bend/exchange/`) | What |
|---|---|---|
| `ledger.py` | `ledger.bend` | accounts, transfer rules, escrow floor, credit limit, history |
| `contracts.py` | `contracts.bend` | the JSON Schema subset, `validate_terms`, contract records, Python value semantics (`py_eq_*`) |
| `contracts.py` (`TRANSITIONS`), `settlement.py` (`outcome_for`), `exchange.py` (`instalments`, `split`, `clip_schedule`, `due_ticks`) | `rules.bend` | the lifecycle, outcomes and the licence fee schedule |
| `settlement.py` | `settlement.bend` | supply settlement arithmetic |
| `exchange.py` (`apply`, every `_op_*`, `state`, `check_invariants`), `tenders.py` (`auction`) | `exchange.bend` | record validation and application |
| (Python `int`) | `int.bend` | arbitrary-precision integers (two's complement bit lists; Bend's `Nat` is unary and `U32` wraps) |
| (Python `json`) | `json.bend` | a JSON parser and a printer byte-equal to `json.dumps(x, sort_keys=True, separators=(",", ":"))` |
| | `main.bend` | the command-line program (the only module with IO) |
| | `LAWS.bend`, `PROOF.bend`, `proof_int.bend`, `proof_nat.bend` | the laws and their proofs |

Not ported: tender generation (`draw`, SHA-256), the command methods (`offer()` and the other `rh` verbs),
the agent views (`public_view`, `market`), `rescore`, `payment_timing`, and everything in `multi/`.

## 2. The command-line contract

```
bend/build/waybill-ex <records.jsonl>
```

Input: one exchange record per line, each written by `json.dumps(record)`. Output: exactly one line,
canonical JSON (`sort_keys`, compact, ASCII):

- `{"ok":<state>}`: every record applied and the invariants hold; `<state>` is `Exchange.state()`;
- `{"error":"<code>","seq":<n>}`: record `n` (0-based line index) was rejected with that `ExchangeError` code.
  `"internal"` stands for a Python exception that is not an `ExchangeError` (an unhashable `op` or `kind`);
  `"invariant"` for a failed `check_invariants` after the last record (only an empty log reaches it).

Exit status 0 in all these cases; non-zero for a usage or IO error, or a line that is not JSON (exit 3).

The Python side, `railhead.exchange.bend.reference(records)`, makes the same line from the Python exchange.
`railhead.exchange.bend.normalize` writes the records the way Bend reads them (a JSON round trip of each record,
so numbers arrive in Python's own text: ints exact, floats in `repr` form).

## 3. How it is verified

"Correct" means byte equality with the Python reference. The oracle is in `src/railhead/exchange/bend.py`
and `src/railhead/exchange/corpus.py`; the corpus is seeded and deterministic.

| Input | Source | Cases checked | Differences |
|---|---|---|---|
| Real logs | the `ex` records of each recorded campaign game (data stays outside the repo) | 14 of 14 games | 0 |
| Sessions | random companies drive the real command API: both arms, rules 1 and 2, credit limits, start balances, public and addressed offers, supply and licences, counters, amendments, expiries, tenders, settlements, obligations, tagged and untagged payments, unicode messages, integers up to about 2^70 | 13,300 | 0 |
| Mutations | each session mutated once (dropped, extra or renamed fields, wrong `seq`, reversed tick, int to float or bool or string, unknown ids, wrong awards and outcomes, overpayments, huge and negative ints) and truncated after the mutation | 23,300 | 0 |
| Edge cases | records the API cannot make: missing optional fields, odd `settle.parts`, floats and bools at Python's `==` sites, NaN and Infinity, falsy `rules` | 266 per run | 0 |
| Captured | every record sequence the existing exchange unit tests apply, accepted and rejected | 12,036 (all of them) | 0 |
| JS build | the same program compiled to JavaScript, on a sample | 1,866 cases and 34 logs | 0 |

Each layer also has its own tests against Python: `int.bend` (about 6,000 operations up to 2^200, every sign
case of floor division), `json.bend` (4,500 random documents with every escape class, every Unicode plane and
lone surrogates), `ledger.bend` (13,300 transfer lines over 300 seeds) and `rules.bend` (3,000 cases).

The corpus reaches every error code that `Exchange.apply` can raise. Four codes in the Python package cannot
come from `apply`: `bad_snapshot` (tender generation only), `escrow_mismatch` (`settle_supply`, which `apply`
does not call), `bad_transition` (every move is checked earlier) and `unknown_account` (accounts are checked
first).

Python behaviour that Bend reproduces on purpose:

- the first error wins, so every check runs in Python's order (for example, `credit_limit` before the company ids);
- `is_int` is not true for a bool; `rules = r["rules"] or 1`, so `True` acts as rules 1 and `2.0` as rules 2;
- the `==` checks (`seq`, tender awards, obligation settlements, licence settlements) follow Python: `True == 1`,
  `1.0 == 1`, and a float equals an int only if its exact double value is that int;
- a float or bool `seq` is stored as written in messages and bids; `settle.parts` is stored as written;
- dict keys sort by code point; a valid surrogate pair is one character for the message length limit.

## 4. The laws

`bend/exchange/LAWS.bend` states them; `PROOF.bend` proves them; `bend PROOF.bend --verdict` checks the
proofs again with the BendTT kernel, which has a proof in Lean. The laws are about the code that runs, not a
model of it, and nothing they depend on uses `@unsafe` or foreign code.

| Group | Laws | Statement |
|---|---|---|
| L1 lifecycle | `terminal_no_successor`, `terminal_no_move`, `move_allowed` | settled, expired and withdrawn have no successor; a successful move gives the requested status, and only a status the transition table allows |
| L2 outcomes | `outcome_kept`, `outcome_broken`, `outcome_partial` | kept iff not delivered < quantity; broken iff delivered = 0 and delivered < quantity; partial otherwise |
| Integers | `is_eq_sound`, `is_eq_complete`, `add_value`, `sub_value`, `neg_value`, `add_comm`, `add_assoc`, `sub_add`, `eq_refl` | equality is exact; addition, subtraction and negation compute the integer sum, difference and negation |
| L4 conservation | `transfer_conserves`, `new_total_zero`, `built_total_zero` | a transfer, successful or not, forced or not, between any accounts, keeps the sum of all balances; a new ledger sums to 0; a ledger built only by transfers from a new ledger sums to 0 |
| L5 fee split | `split_sums` | the instalments of a licence fee sum to the fee |

What the laws do not cover: multiplication, division, the order tests, decimal reading and printing (these
are tested against Python, not proven); and anything outside `int`, `ledger` and `rules`. Equal values are
stated with `I.is_eq`, which is itself proven exact. Python's per-transfer assertion that the balances sum to 0
is not repeated at run time in Bend: L4 proves it.

## 5. Use

```bash
sh bend/install-bend.sh --lean                       # pinned Bend 2.0.40 (sha-checked) and the Lean kernel toolchain
(cd bend/exchange && ~/.bend/bin/bend PROOF.bend --verdict)   # ALL PROOFS CHECK
python -c "from railhead.exchange.bend import build; build()" # bend/build/waybill-ex
railhead waybill-bend verify runs/s5-enforced runs/s5-cheap   # one line per log: match <n> or DIFF <path>
railhead waybill-bend corpus --out /tmp/corpus --kind all --seed 1 --count 1000
```

`verify` takes run directories, merged logs or files of exchange records, and exits 1 on any difference.
The binary is found by `RAILHEAD_BEND_EX`, else `bend/build/waybill-ex`; `bend` by `RAILHEAD_BEND`, the
`PATH` or `~/.bend/bin/bend`. The CI job `bend` installs the pinned compiler, checks the proofs with
`--verdict`, builds the binary and runs the differential tests and a corpus sweep.

## 6. Limits

- **Very long licences.** The fee schedule has one instalment per quarter, and both implementations build
  the list. Python builds a list of Python ints; Bend counts the quarters with a unary `Nat`. A licence of
  millions of quarters runs either one out of memory, at different sizes. Game licences have a few quarters.
- **Integer size.** Neither side limits it. CPython refuses by default to turn an int of more than 4,300
  digits into text; that is an interpreter guard, not an exchange rule, so the oracle lifts it while it
  writes and compares JSON (a computed obligation of 10^4400 is a test case).
- **Speed.** A recorded game (up to about 500 records) replays in 30 ms or less. Cost grows faster than the
  log: a synthetic log of 9,177 records and 2,500 contracts takes 15 seconds, mostly list scans over contracts
  and obligations.
- **The compiler is young.** Bend 2 was released in September 2026; the port is pinned to 2.0.40 and the
  native and JavaScript builds are both checked against Python.

No input that both implementations can process gives different output.
