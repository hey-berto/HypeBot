# Epoch006 Day-42 right-censoring shadow diagnostic

**NON_BINDING_RIGHT_CENSORING_SENSITIVITY.** This is a post-cutoff,
read-only explanatory report. It does not change `PHASE3_ANALYSIS_GATE_V1`,
paired outcomes, bootstrap, ESS, thresholds, or any `PROMOTE`, `REJECT`, or
`INCONCLUSIVE` decision.

## Fixed scope

The only supported cutoff is `2026-11-05T00:15:00Z`. Run the reader against a
read-only copy of the epoch006 database frozen at that instant; do not run it
against a later mutable working database. Stored `paper_trades` are updated as
the simulator progresses, so a later copy cannot recreate funding accrued at
the cutoff for a position subsequently closed. This is a schema limitation, not
a reason to modify evidence.

Inputs are the frozen-at-cutoff `paper_trades`, eligible scored-prospective
snapshot regime, and the latest HYPE 1m raw candle with both `close_time <=`
and `received_at <=` the cutoff. The frozen fee and slippage bps must be passed
from the validated epoch006 simulator configuration and recorded with the run;
they are not inferred from outcome data. The schema does not store these
per-trade configuration values separately.

## Terminal valuation

For each `OPEN` position, the raw cutoff close is adjusted with the frozen
adverse exit slippage. The report then applies the frozen exit fee, adds exit
slippage to the stored entry slippage, and preserves the stored, already-accrued
funding cost. Its net value uses the simulator's existing accounting:

`sign × (cutoff_raw_price − entry_price) − (stored_fees + exit_fee) −
(stored_slippage + exit_slippage) − stored_funding_cost`.

`PENDING_ENTRY` positions are counted and listed separately. No entry, price,
return, or counterfactual outcome is invented for them.

The output contains both `PRIMARY_PHASE3_RESULT` (passed through unchanged,
with right censoring explicitly retained) and
`NON_BINDING_RIGHT_CENSORING_SENSITIVITY`. The latter contains per-position
MTM, strategy totals, open/pending counts, and position age, direction, regime,
and unrealized-PnL sign. The duration/sign view is descriptive only and creates
no hypothesis test.

## Post-cutoff procedure

1. At the cutoff, preserve a read-only database copy and verify its integrity.
2. Obtain the existing formal Phase 3 result without changing it.
3. Obtain the validated frozen simulator fee/slippage values; do not substitute
   values from realised outcomes.
4. Call `collect_right_censoring_shadow` with the frozen copy, economics, and
   the formal result mapping.
5. Store the output separately from the formal result and label it
   `NON_BINDING_RIGHT_CENSORING_SENSITIVITY`.

Limitations: this is a single-price terminal sensitivity, does not model a new
order book, does not alter right-censoring, and cannot reconstruct a cutoff
state from a database that has since progressed those positions.
