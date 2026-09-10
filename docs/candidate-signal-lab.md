# Candidate Signal Lab and safety infrastructure

This branch contains an isolated historical-research lab and deterministic risk
receipt infrastructure. Neither component imports an active-epoch repository,
writes to a Phase 1/2 database, calls an LLM, or can submit an order.

## Batch 1 freeze

`config/phase3/candidate_signal_lab_batch1.yaml` freezes the seven requested
HYPE-specific hypotheses, definitions, provenance fields, causal cutoffs,
normalization windows, event threshold, 1h/4h primary horizons, 15m/12h
exploratory horizons, chronological 60/40 fit/confirmation split, 14-test
BH-FDR family at q=0.10, stationary-bootstrap method, and minimum event counts.

The configuration status is `FROZEN_NOT_EXECUTED`. A real historical Stage-1
run is a separate action requiring explicit authorization. The runner rejects
any source whose dataset identity begins with `phase2_epoch_`; results can
never be pooled into the active prospective epoch.

The Lab database is restricted to `data/phase3_signal_lab/`. Its manifest,
hypothesis, source and run rows are append-only through update/delete triggers.
Failed, weak, rejected and data-invalid hypotheses remain registered. A changed
definition must use a new hypothesis ID.

## Data quality and causality

The source contract requires exact provenance, a content hash, a reproducible
locator, UTC timestamps and explicit timestamp semantics. Validation records
monotonicity, duplicates, missing intervals, field coverage and proof of
aggressor-side semantics. No forward fill is implemented. Forward returns are
joined only at exact future timestamps after each causal signal has already
been computed.

Trade flow uses explicit taker/aggressor sides: `BUY` means taker buy and
`SELL` means taker sell. Aggregation intervals are start-inclusive and
end-exclusive. Duplicate trade IDs fail closed. Average aggressor trade size
is retained only as a descriptive control and is not a separate hypothesis.

Current real-data readiness is intentionally conservative:

- completed HYPE/BTC candles and funding can be validated from reproducible
  venue data;
- open-interest history requires a complete timestamped source before the two
  OI hypotheses can be screened;
- historical trade prints require reproducible aggressor-side coverage before
  the three order-flow hypotheses can be screened;
- liquidation/forced-flow data is not part of Batch 1.

Insufficient coverage produces `DATA_INVALID`; no proxy substitution is
allowed.

`initialize_frozen_infrastructure` creates only the isolated Lab and Risk
stores, registers the frozen manifest and policy, and fails if either store
already contains results. It does not expose a Stage-1 command. The committed
implementation identity must be supplied and is stored alongside the manifest
before any later Batch 1 authorization.

## Statistical contract

Each signal is computed from observations at or before timestamp `t`. Event
outcomes are directional forward log returns. The same frozen definition is
used in a contiguous chronological fit period and held-out confirmation
period. A primary result can pass only when fit and confirmation signs agree,
event-count gates pass, and its confirmation p-value survives the single
14-member Benjamini-Hochberg family. Exploratory horizons cannot promote.

`PASS_STAGE_1` means only that a feature deserves Stage-2 economics work. It is
not a profitable strategy claim and cannot alter the active epoch.

## Risk receipts

`config/phase3/risk_policy_v1.yaml` is intentionally fail-closed and defaults
the kill switch on. The pure risk engine emits a hashed receipt with the
complete intent, account-state hash, policy identity, ordered gate results,
approved/modified/rejected disposition, deterministic reason codes and allowed
notional/leverage. The isolated store is restricted to `data/phase3_risk/` and
is append-only.

The implementation has no SDK client, network call, wallet, key handling,
withdrawal method or order-submission method. Testnet and mainnet are not
enabled.
