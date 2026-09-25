# Epoch006 frozen V2 GateEvidence adapter

**Status: IMPLEMENTED_SYNTHETIC_ONLY_PENDING_INDEPENDENT_REVIEW.** The adapter
is implemented at
`src/hype_autopilot/phase3/gate_evidence_adapter_v2.py` strictly against
`PHASE3_EPOCH006_DERIVATION_SPEC_V2`, canonical SHA-256
`d5387568a639af105165a8e00705edef1509145706dce58519d2e56b1ff3e629`.
Implementation does not authorize an evaluation or production epoch006 outcome
access. Day-42 production use remains blocked until this adapter and its golden
fixtures receive independent review.

The adapter opens SQLite with `mode=ro` and `PRAGMA query_only=ON`, validates
the frozen V2 authorization and dependency hashes before opening the database,
then fails closed on source, manifest, schema, evidence-clock, strategy,
lineage, regime, cutoff, or numeric inconsistencies. It returns `GateEvidence`
but never calls the formal evaluator.

Synthetic coverage and the explicit expected output are in:

- `tests/test_phase3_gate_evidence_adapter_v2.py`
- `tests/fixtures/phase3_epoch006_gate_evidence_v2_golden.json`

Coverage includes TRADE/NO_TRADE, CLOSED and right-censored OPEN/pending states,
position suppression, pair-specific co-eligibility, deterministic ordering and
hash tiebreaking, regime linkage, stored-return preservation, full shared API
cost normalized to one-HYPE entry or no-entry reference notional, SQLite float
to Decimal HALF_EVEN conversion, malformed lineage, frozen-identity mismatch,
and entries before, exactly at, and after the formal cutoff. The synthetic
trades deliberately contain nonzero fee, funding, and slippage fields while
the golden output uses stored `return_pct`, proving those embedded simulator
costs are not deducted again.

No production epoch006 database was opened, no evaluation was executed, and no
live runtime, configuration, scheduler, evidence, simulator, evaluator, or gate
file was changed during implementation.
