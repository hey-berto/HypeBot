# Epoch006 non-binding diagnostic package

**Classification: `NON_BINDING_DIAGNOSTIC`.** This is a local, read-only shadow
layer. It does not import, invoke, or alter `PHASE3_ANALYSIS_GATE_V1`, its
evidence, thresholds, bootstrap, or PROMOTE / REJECT / INCONCLUSIVE logic.

## Source and separation

The reader opens SQLite only with `mode=ro` and `PRAGMA query_only=ON`. It may
later consume a read-only epoch006 source, but is not deployed on mmt2 and does
not write reports, receipts, or state. Every output carries
`NON_BINDING_DIAGNOSTIC` and `NONE_NOT_AN_INPUT_TO_PHASE3_ANALYSIS_GATE_V1`.

## Hybrid veto-value decomposition

For each Hybrid/Quant pair, the Quant opportunity set is scoreable prospective
snapshots with a non-`NO_TRADE` Quant decision and an observed Hybrid decision.
The package reports retained and vetoed counts by contemporaneous `(Quant
direction, regime)` stratum.

Within each stratum, a deterministic SHA-256 ordering retains the same count as
the observed Hybrid. This matched control uses snapshot hash, fixed diagnostic
version, and strategy identity only; it has no outcome access, no future data,
and no epoch006 tuning. It can separate an observed veto pattern from equally
frequent generic suppression when a later review separately evaluates prepared
groups. It cannot prove LLM causality or remove all exposure/fee effects, and
it never creates a primary strategy.

## Fail-closed selection

The package compares `VALID` and `FAIL_CLOSED` LLM rows using contemporaneous
combined regime, UTC hour, realised volatility, ATR percentage, funding rate,
funding z-score, and fail-closed reason counts. It can reveal operational
clustering that warrants review. It is descriptive association only: it cannot
establish causality or a selection benefit. Frozen snapshots do not contain a
precomputed market-stress composite or intrabar-range field, so those are
explicitly reported unavailable rather than reconstructed.

## Co-eligibility representativeness

For each preregistered pair, the package compares all scoreable prospective
snapshot regimes with canonical `phase2_pair_outcomes` marked `CO_ELIGIBLE`.
It reports row coverage, all/co-eligible/excluded counts, outcome status counts,
regime proportions, and total-variation distance. It can expose position-state
and suppression selection. It does not construct trade returns or
counterfactuals for excluded periods and does not alter right-censoring.

## Return-component decomposition

For closed paper trades only, the package reports frozen `paper_trades`
accounting components: gross price PnL, funding, entry/exit fees, simulator
slippage, and unchanged stored net PnL. It can explain accounting composition;
it does not recompute, replace, normalize, or feed the primary realized net
return. Open and pending positions are excluded, not marked to market.

## Existing fields and limitations

| Diagnostic | Frozen fields used | Limitation |
|---|---|---|
| Hybrid veto | scoreability, Quant/Hybrid decisions, regime, snapshot hash | no causal identification; no outcome access for assignment |
| Fail-closed | LLM status/reason, snapshot features/timestamp | no stress composite or intrabar range; association only |
| Co-eligibility | snapshots and canonical pair eligibility/outcome rows | absent pair rows remain visible; no excluded-period counterfactual |
| Components | closed-trade gross PnL, funding, fees, slippage, net PnL | no open-position component or primary-return replacement |

No runtime telemetry, configuration, schema, model, prompt, or primary
evaluation methodology is changed by this package.
