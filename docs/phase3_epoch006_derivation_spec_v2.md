# PHASE3_EPOCH006_DERIVATION_SPEC_V2 — draft

**Status: DRAFT_PENDING_INDEPENDENT_REVIEW.** This is an additive blinded
amendment for the unchanged epoch006 evidence window. V1's evaluator and gate
remain unmodified, but V1 was under-specified because it did not define the
frozen-DB-to-`GateEvidence` derivation layer. Any result under this document is
an **AMENDED BLINDED PRIMARY ANALYSIS**, not an unqualified V1 result.

The canonical machine-readable draft is
`config/phase3/epoch006_derivation_spec_v2.yaml`; its canonical serialization
is recorded in `epoch006_derivation_spec_v2.canonical.sha256` as
`4dc1724829aaedb401b669de9daab800761e238237f9717be002972ddaef7bce`.
Independent review must re-compute it and record the final-byte hash before any
adapter implementation.

## Exposure disclosure

Authors did **not** see epoch006 PnL, returns, expectancy, win rates,
comparative strategy performance, or Day-42 outcomes. Previously visible
information includes first-four validation health/identity results, database
integrity and gap counts, process/timer telemetry, historical sample-rate
planning, and synthetic fixture outputs. No quantified epoch006 co-eligibility
example, including a possible previously discussed “3/17”, is adopted as a
decision input in this draft; the current record does not establish it as a
reviewed author exposure fact. This is an outcome firewall, not a claim of
complete blindness to operational or sample-availability information.

Every new convention below is selected from frozen source semantics, scale and
unit consistency, deterministic reconstruction, and conservative comparison
accounting—not from epoch006 sample characteristics or outcomes.

## Canonical reconstruction

Population is exactly scoreable `SCORED_PROSPECTIVE` snapshots from
`2026-09-24T00:15:00Z` through `2026-11-05T00:15:00Z`, inclusive. A rejected or
non-scoreable boundary is excluded permanently; V2 never reinterprets it.
Post-cutoff evidence is rejected.

At each boundary, frozen runtime order is simulator progression, snapshot,
decisions, then paper-trade submission. Thus both legs are flat only when their
prior active intervals satisfy `signal_time < boundary` and `(exit_time is NULL
or exit_time > boundary)`. Both flat means `CO_ELIGIBLE`; otherwise
`NOT_CO_ELIGIBLE` and `EXCLUDED`. A co-eligible pair is `RIGHT_CENSORED` when
either leg is pending/open, and is `COMPLETE` otherwise. Pending and open legs
receive no MTM in primary evidence.

## Returns and costs

The selected paired unit is stored `paper_trades.return_pct`: it is net of
entry/exit fees, funding, and simulator slippage, as implemented by the frozen
simulator. `NO_TRADE` and completed suppressed legs receive zero. This is scale
invariant; `net_pnl` is not, and `r_multiple` depends on strategy-specific stop
geometry. The choice is a **PRINCIPLE_DERIVED_NEW_CONVENTION**, not frozen
precedent.

For a complete pair: `difference = treatment_return_pct - control_return_pct`.
`cost_adjusted_difference = difference - treatment_api_cost/snapshot_price +
control_api_cost/snapshot_price`, using the shared snapshot's
`market.hype_features.last_15m_close`. API cost is the stored LLM invocation
cost. The full cost is charged to every LLM-dependent treatment comparison
(direct LLM or Hybrid) and zero to Quant control. This makes each comparison
individually cost-complete, but costs must never be summed across comparisons.
It is a **PRINCIPLE_DERIVED_NEW_CONVENTION** and may change formal verdicts.

## Remaining derived fields

Triggered trades are one `CLOSED` paper trade attributable to an in-population
strategy decision. Directional, pending, open, and suppressed states do not
count. Trend/volatility/bucket are the shared snapshot's `regime.trend`,
`regime.volatility`, and `regime.combined`; a missing label rejects the package.
Right-censored pairs contribute one duration, `cutoff - snapshot_timestamp`,
whether one or both legs are pending/open. Multiple active positions per
strategy violate the frozen unique-active-position invariant and reject input.
These are **PRINCIPLE_DERIVED_NEW_CONVENTION** choices.

Complete pair observations are ordered by ascending snapshot timestamp then
snapshot hash. The timestamp is unique per epoch/observation class, and this is
the frozen time-series precedent used by repository readers. This order affects
ESS, stationary bootstrap, half splits, and remove-best analysis.

All numeric outputs are decimal-quantized to ten places, `ROUND_HALF_EVEN`,
and serialized using `canonical_json`; signed zero is canonical zero. Numeric
series contain only complete finite observations. Missing/non-applicable fields
are `null` outside those series.

No adapter may be implemented until this draft has no unresolved consequential
fields, its golden fixtures are complete, an independent reviewer approves it,
the final bytes and SHA-256 are recorded, and the outcome-access firewall is
still intact.
