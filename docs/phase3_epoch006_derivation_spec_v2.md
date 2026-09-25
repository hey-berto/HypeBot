# PHASE3_EPOCH006_DERIVATION_SPEC_V2 — revised draft

**Status: REVISED_DRAFT_PENDING_INDEPENDENT_FREEZE_REVIEW.** This is an
additive blinded amendment for the unchanged epoch006 evidence window. It does
not alter the V1 evaluator or gate and does not authorize a `GateEvidence`
adapter. Any eventual result under this document is an **AMENDED BLINDED
PRIMARY ANALYSIS**, not an unqualified V1 result.

## Version history

The original V2 draft remains identifiable at commit
`5c425adb6230f3b94c959d208245b36879e2ad9c`, canonical SHA-256
`4dc1724829aaedb401b669de9daab800761e238237f9717be002972ddaef7bce`,
and review disposition `SECONDARY_V2_REVIEW_ADDITIONAL_CHANGES_REQUIRED`.
This revision supersedes that draft without erasing it from Git history. Its
revised canonical SHA-256 is
`d5387568a639af105165a8e00705edef1509145706dce58519d2e56b1ff3e629`,
recorded beside the machine-readable specification. The amendment resolves
the secondary review's denominator, dimensional consistency, cost embedding,
allocation objective, trigger, interval, runtime-evidence limitation, regime,
ordering, numeric conversion, exposure, fixture, and materiality findings.

## Exposure disclosure

Project participants and reviewers had previously seen operational and sample
information, including first-four health, identity hashes, integrity/gap and
process telemetry, historical sample-rate planning, synthetic fixtures, and
the historical/example co-eligibility figure **“3/17.”** They had not seen
epoch006 returns, PnL, expectancy, win rates, comparative performance, or
Day-42 outcomes. The rules below are based on frozen source behavior,
dimensional consistency, deterministic reconstruction, and conservative
accounting—not on the observed operational count.

## Population and pair reconstruction

Population is exactly scoreable `SCORED_PROSPECTIVE` snapshots from
`2026-09-24T00:15:00Z` through `2026-11-05T00:15:00Z`, inclusive. Rejected or
non-scoreable boundaries are permanently excluded. Post-cutoff evidence
rejects the package.

For a logical boundary `b`, a strategy is active exactly when a persisted
trade has `signal_time < b` and (`exit_time IS NULL` or `exit_time > b`). Thus
a signal exactly at `b` is not a prior active position, and an exit exactly at
`b` is already flat. Both comparison legs must be flat under that rule to be
co-eligible.

`signal_time` is not uniformly a wall-clock write time. `PaperSimulator.submit`
copies `StrategyDecision.created_at`: Quant sets it to the logical snapshot
boundary; LLM sets it to the provider request end; Hybrid uses the later of
the Quant timestamp and LLM request end. Terminal suppressed trades without an
`exit_time` use their persisted `last_processed_at` as the interval end, as in
the existing read-only operational reconstruction. `SUPPRESSED_POSITION_OPEN`
does not create a second interval because the pre-existing active position is
the governing interval.

The frozen runtime sequence is:

1. progress existing simulator state through `boundary`;
2. build and persist the boundary snapshot;
3. produce the five strategy decisions;
4. submit new paper trades.

This sequence is authoritative in frozen runtime code, but it is **not
independently persisted as stage-level event evidence**. Individual snapshots,
decisions, trades, orders, fills, and cycle status are persisted; their
stage-by-stage ordering is reconstructed by trusting the frozen code. This is
a known limitation, and V2 does not invent stronger evidence.

## Return denominator and embedded simulator costs

The live incremental simulator defines:

```text
entry_price = raw_entry_price adjusted for entry slippage
gross_pnl = direction_sign * (raw_exit_price - entry_price)
net_pnl = gross_pnl - entry_fee - exit_fee
          - stored_entry_slippage - stored_exit_slippage - funding_cost
return_pct = net_pnl / entry_price
```

There is no position-size or quantity field. Prices, PnL, fees, funding, and
slippage are therefore per an implicit fixed quantity of **1 HYPE**. The exact
denominator of stored `return_pct` is
`entry_price_USD_per_HYPE × 1_HYPE`, i.e. the one-HYPE entry notional in USD.
It is not margin or allocated capital.

The source also establishes this invariant:

```text
SIMULATOR_COSTS_ALREADY_EMBEDDED_IN_RETURN_PCT = true
```

Entry and exit fees, funding, and entry/exit slippage are already embedded.
On the live incremental path, entry slippage affects the slipped `entry_price`
used in `gross_pnl` **and** is included again in `total_slippage`; that
double-incidence is frozen simulator behavior, not a V2 correction target.
Exit slippage is included through `total_slippage`. The adapter must consume
stored `return_pct` and must never deduct any of these simulator costs again.
`NO_TRADE` and completed suppressed no-entry legs receive `0.0`; pending/open
legs are right-censored and have no numeric return.

## Dimensionally valid API-cost adjustment

V2 asks the **standalone architecture economics** question: “Would this
individual LLM-dependent architecture justify its own full inference cost?”
It does not estimate the economics of one shared multi-strategy deployment.

For architecture `s` at a complete co-eligible observation:

```text
adjusted_return_s = return_pct_s - api_cost_return_s
api_cost_return_s = api_cost_usd_s / relevant_one_hype_notional_usd_s
cost_adjusted_pair_difference = adjusted_return_treatment
                              - adjusted_return_control
```

When `s` actually entered, the denominator is its persisted
`entry_price_USD_per_HYPE × 1_HYPE`, timestamped at `entry_time`. When it did
not enter (`NO_TRADE` or a completed suppressed no-entry leg), the denominator
is the shared snapshot's `last_15m_close_USD_per_HYPE × 1_HYPE`, timestamped at
the shared `snapshot_timestamp`. This no-entry reference is required because
the inference cost exists even when the architecture declines or fails to
enter, while no actual entry notional exists. A missing, non-finite, or
non-positive selected denominator rejects the package; division by zero is
never permitted. Pending/open pairs remain right-censored and are absent from
both numeric series.

The API cost is the recorded `llm_decisions.model_cost_usd` for the shared
snapshot. Quant controls receive zero. Direct LLM and each Hybrid comparison
receive the full recorded shared-call cost because each comparison answers the
standalone question. A Hybrid that entered uses its own entry notional; a
Hybrid with no entry uses the shared snapshot one-HYPE reference notional.
**Never sum API costs across separate comparison outputs to estimate total
system spend**: the same shared call is intentionally charged to multiple
standalone questions and such a sum would double count it.

### Dimensional analysis

| Term | Unit | Source/timestamp | Use |
|---|---|---|---|
| `net_pnl` | USD per 1 HYPE position | closed paper trade | numerator of stored return |
| `entry_price` | USD/HYPE | persisted `entry_time` | actual-entry denominator price |
| fixed quantity | HYPE | source-implied constant `1` | converts price to USD notional |
| actual entry notional | USD | `entry_price × 1 HYPE` | denominator when entered |
| snapshot reference price | USD/HYPE | shared `snapshot_timestamp` | no-entry denominator price |
| no-entry reference notional | USD | snapshot price × `1 HYPE` | denominator when no entry exists |
| `return_pct` | dimensionless | `net_pnl / actual entry notional` | unadjusted architecture return |
| `model_cost_usd` | USD | shared LLM decision | inference cost numerator |
| `api_cost_return` | dimensionless | USD / USD | cost comparable to `return_pct` |
| adjusted pair difference | dimensionless | treatment adjusted return − control adjusted return | gate cost series |

## Triggered-trade minimum

V2 selects **actual executed entry**, not directional signal, submission, or
completed round trip:

| Candidate | Opportunity generation | Realized evidence | Censoring/cutoff behavior | Deterministic reconstruction |
|---|---|---|---|---|
| Directional decision | measures intent only | may never submit or enter | overcounts suppressed/pending cases | decision row |
| Submitted/pending trade | measures accepted submission | may never fill | overcounts stale pending entries | trade/order row |
| **Actual executed entry** | **measures genuinely triggered opportunity** | **entry is real; return may remain censored** | **entered-open trades count while their returns remain censored** | **non-null `entry_time`, status OPEN/CLOSED** |
| Completed closed trade | measures realized round trips | strongest realized evidence | undercounts genuine near-cutoff entries already handled by right censoring | status CLOSED |

Count one non-excluded, in-population strategy trade with non-null
`entry_time <= formal_cutoff`; `OPEN` and `CLOSED` count. `NO_TRADE`, pending,
and all suppressed no-entry statuses do not. This makes the threshold measure
genuinely triggered/executed opportunities while the separate right-censoring
rule controls whether a return may enter the paired series.

## Regimes, ordering, and numeric canonicalization

Trend domain is `{UP, DOWN, RANGE, UNKNOWN}`; volatility domain is
`{LOW, NORMAL, HIGH, UNKNOWN}`. `combined` is persisted in the snapshot but
the classifier deterministically derives it as `trend + "_" + volatility`,
yielding the 16 Cartesian labels. Scoreable evidence permits only the nine
non-`UNKNOWN` combinations. A non-scoreable snapshot is excluded. For an
included scoreable snapshot, one missing marginal, a missing combined value,
an `UNKNOWN`, an out-of-domain label, or `combined` inconsistent with the two
marginals rejects the entire package rather than silently excluding or
repairing the row.

Complete observations sort by `(snapshot_timestamp, snapshot_hash)`. This is
a total order. `snapshot_hash` is the existing immutable Phase 2 SHA-256 of the
canonical snapshot payload, not a new V2 hash. Production uniqueness normally
prevents an epoch/class timestamp tie, but the tiebreak remains normative and
is covered by a synthetic identical-timestamp fixture.

SQLite numeric columns are `REAL`; repository writes bind Python floats and
SQLite reads return Python floats. The only canonical path is:

```text
source_float -> Decimal(str(source_float))
             -> quantize(Decimal("0.0000000001"), ROUND_HALF_EVEN)
             -> canonical_json fixed 10-place serialization
```

Direct `Decimal(source_float)` is forbidden. Non-finite values reject the
package and signed zero becomes `0.0000000000`.

## Implementation gate

No adapter may be implemented until this revision has no consequential
unresolved fields, all expanded synthetic fixtures are complete, an
independent reviewer approves the exact revised bytes, the final canonical
hash is frozen, and the outcome-access firewall remains intact.
