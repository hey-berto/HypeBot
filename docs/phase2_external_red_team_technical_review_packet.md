# HYPE Autopilot Phase 2 — External Technical Review Packet

## Scope

This packet is a frozen-source/schema review aid for epoch006. It intentionally
omits strategy returns, PnL, expectancy, win rates, and all interim performance.
It makes no recommendation and authorizes no runtime change.

- **SOURCE-CODE FACT** — defined by reviewed source/schema.
- **FROZEN CONFIG FACT** — epoch006 contract value.
- **PHASE-3 EVALUATION RULE** — downstream reconstruction behavior.
- **KNOWN LIMITATION** — bounded telemetry/evidence statement.

## 1. Evidence database and relationships

**SOURCE-CODE FACT.** Immutable snapshots and epoch records have update/delete
triggers. Phase 2 manifests, attempts, LLM decisions, pair outcomes, recovery
events, exclusions, deployments, and evidence windows also have immutability
triggers.

```text
phase2_manifests                 phase2_recovery_events
         │                                 │
research_cycles ──snapshot_hash──> decision_snapshots <── phase2_evidence_windows
         │                          │
         │                          ├── snapshot_source_references
         │                          ├── strategy_decisions ──> paper_trades
         │                          │       │                    ├── paper_orders
         │                          │       └────────────────────└── paper_fills
         │                          ├── detector_decisions
         │                          ├── llm_invocation_attempts
         │                          ├── llm_decisions
         │                          └── phase2_pair_outcomes
         └── details_json

phase2_outcome_exclusions ──> paper_trades
collection_gaps, data_quality_events, health_events, experiment_events
```

| Table | Contract role |
|---|---|
| `research_cycles` | One lifecycle row per `(scheduled_at, observation_class)`: logical boundary, operational start/completion, status, snapshot link, audit details. |
| `decision_snapshots` | Immutable canonical input keyed by `snapshot_hash`; unique by epoch, logical timestamp, and observation class; has `scoreable` and canonical JSON. |
| `snapshot_source_references` | Named source timestamps for a snapshot. |
| `strategy_decisions` | One row per snapshot/strategy/version, with full decision payload and selected model telemetry. |
| `detector_decisions` | One detector observation per snapshot/detector version; diagnostic context, not an execution submitter. |
| `llm_invocation_attempts` | Immutable numbered attempt per epoch/snapshot; provider state, tool count, raw-capture/hash, payload, integrity hash. |
| `llm_decisions` | One immutable LLM decision per epoch/snapshot/version; `VALID` or `FAIL_CLOSED` status/reason. |
| `paper_trades`, `paper_orders`, `paper_fills` | Stateful simulated position, deterministic order, and entry/exit fill records. One active trade per strategy; one order per decision; fills unique per trade/type/time. |
| `phase2_manifests` | Frozen activation source/config/prompt/output-schema/DB-schema identities plus contract payload. |
| `phase2_evidence_windows` | One evidence window per epoch; fresh-start window links to the prospective-start integrity event. |
| `phase2_recovery_events` | Immutable worker start, prospective anchor, downtime, and recovery evidence. |
| `phase2_pair_outcomes` / `phase2_outcome_exclusions` | Paired-comparison bookkeeping and exclusions. |
| `collection_gaps` | Source-data gap detection/recovery state. |

## 2. Timestamp semantics

| Field/event | Meaning |
|---|---|
| Manifest `activation_timestamp` | Authorization time, not evidence-clock start. |
| `WORKER_START_ATTEMPT.occurred_at` | Worker-attempt time, persisted before data readiness. |
| `PROSPECTIVE_START_ESTABLISHED.occurred_at` | Immutable prospective anchor after readiness. |
| Evidence-window `first_eligible_boundary` | First UTC quarter-hour strictly after the anchor; evidence-clock start. |
| Cycle `scheduled_at` | Logical boundary, not actual start time. |
| Cycle `started_at` / `completed_at` | Operational lifecycle times. |
| Snapshot `snapshot_timestamp` | Logical causal evidence time. |
| Snapshot `created_at` | Build/persistence time, distinct from logical time. |
| Attempt `started_at` / `ended_at` | Provider request timing. |
| Paper `signal_time` | Origination decision time; distinct from entry/exit/processing times. |
| Order `eligible_at` | Decision time plus simulator latency. |
| Fill `fill_time` | Selected one-minute candle close. |

**SOURCE-CODE FACT.** Validator timestamp identity is semantic UTC equality;
`Z` and `+00:00` representations denote the same instant.

## 3. Boundary execution order

**SOURCE-CODE FACT.** For a planned future boundary the pipeline: (1) validates
prospective anchor/window, (2) reasserts manifest identity and exclusive
writer lease, (3) performs provider-free interrupted-persistence recovery,
(4) opens idempotent `RUNNING` cycle evidence, (5) collects/recover-gaps,
(6) progresses existing simulated positions, (7) freezes/persists snapshot,
(8) saves quant and detector results, (9) runs/persists fail-closed LLM
evidence, (10) builds same-snapshot hybrids, (11) submits eligible non-flat
decisions to the simulator, then (12) finalizes `COMPLETE`.

Contained outer-pipeline exceptions finalize the cycle `REJECTED`, with no
snapshot and `scoreable=false`; they do not silently disappear.

**FROZEN CONFIG FACT.** Epoch006 cadence is 15 minutes; request timeout 60
seconds; snapshot-to-call limit 120 seconds; malformed-output retry limit one;
tools and all external information channels are disabled.

## 4. Strategy eligibility, decisions, and suppression

| Component | Source-defined rule |
|---|---|
| `QUANT_TREND_V1` | If scoreable and 1h Donchian/ATR history exists: close above prior high → LONG, below prior low → SHORT; else `NO_TRADE`. Stop is 3× ATR; TTL 2,880 minutes. |
| `QUANT_MR_V1` | Requires funding z-score, 1h RSI/ATR/EMA20. Crowded-long extreme may SHORT only with EMA target above signal close; crowded-short extreme may LONG only with EMA target below signal close. Stop 2× ATR; TTL 720 minutes; invalid target gives `NO_TRADE`. |
| `SETUP_DETECTOR_V1` | Records breakout/context/no-trigger evidence. It never submits a trade itself. |
| `LLM_V1` | Frozen snapshot-only input, strict output schema, snapshot-hash and geometry validation. Any fail-close adapts to `NO_TRADE`. |
| `HYBRID_TREND_LLM_V1` | Uses Quant Trend geometry only when Quant Trend is directional, LLM is `VALID`, snapshot hashes match, and directions agree. Otherwise `NO_TRADE`. |
| `HYBRID_MR_LLM_V1` | The same rule against Quant MR, retaining Quant MR geometry only on agreement. |

| State | Exact distinction |
|---|---|
| Eligible + TRADE | Directional decision with no active trade for that strategy; simulator creates pending trade and order. Actual entry remains conditional on latency and minute data. |
| Eligible + `NO_TRADE` | Persisted normal decision with no submission. This is valid scoreable evidence. |
| Suppressed | Directional decision with an active same-strategy trade becomes `SUPPRESSED_POSITION_OPEN`, not a fresh executable position. Later invalid-target/no-entry states are also suppression states. |
| Rejected cycle | Outer boundary failure: terminal `REJECTED`, non-scoreable, explicit reason/error. Downtime is a separate audit-only rejected form. |
| Fail-closed LLM | LLM decision records reason and `FAIL_CLOSED`; adapted LLM/hybrids are `NO_TRADE`. A scoreable cycle may still be `COMPLETE`. |
| Missing/unobserved | No cycle row for the logical boundary. It is distinct from an observed rejected row. |

## 5. Phase 3 co-eligibility reconstruction

**PHASE-3 EVALUATION RULE.** Defined pairs are LLM/Quant Trend, LLM/Quant MR,
Hybrid Trend/Quant Trend, and Hybrid MR/Quant MR. Both legs must share exactly
one snapshot hash. Both must be flat to be `CO_ELIGIBLE`; otherwise the pair is
`NOT_CO_ELIGIBLE` and `EXCLUDED`. A co-eligible pair with either pending/open
leg is `RIGHT_CENSORED`; only otherwise is it complete.

`NO_TRADE` has zero attributed outcome in a completed pair. Suppressed states
have zero attributed outcome but are not executed. Pending/open positions do
not receive completed realized attribution. Pair records retain direction,
regime, cost, and API-cost fields; no values are reported here.

## 6. Simulator semantics

**SOURCE-CODE FACT.** A directional decision is eligible after three seconds.
The first 1-minute candle whose close is at/after eligibility supplies raw
entry. Long entry is worsened upward by 2 bps; short entry downward by 2 bps;
entry fee is 4.5 bps.

- Missing eligible minute data leaves a pending position in the stateful engine
  (the pure helper represents `SUPPRESSED_NO_ENTRY_DATA`).
- A target on the wrong side of post-latency entry becomes
  `SUPPRESSED_INVALID_TARGET_AFTER_LATENCY`.
- ATR stops are recalculated at actual entry; Trend applies a 3× ATR chandelier
  update as highs/lows evolve.
- Each later minute checks stop before target. A same-bar hit selects stop and
  records `INTRABAR_ORDER_AMBIGUOUS`.
- TTL exits at the qualifying minute close. Exit price is worsened by 2 bps;
  exit fee is 4.5 bps. Funding observations are applied while open.
- Closed positions remain linked to their origination snapshot hash/decision;
  attribution belongs to that snapshot, not the management bar. Open/pending
  positions are right-censored in paired evaluation.

## 7. LLM retry/fail-close and persistence

**SOURCE-CODE FACT.** Before any call, the runner freezes snapshot input,
checks existing/recoverable evidence, requires scoreability/freshness, and
enforces snapshot-only/no-tools. It allows at most two transport attempts: the
initial call plus one retry.

For every response it hashes raw plaintext, applies safe capture, then checks
tool count, sensitive-material policy, completion freshness, model/version,
JSON syntax, strict schema/version, snapshot hash, and geometry. The immutable
attempt is saved before the valid or fail-closed LLM decision. Timeout/error
attempts are saved before retry or terminal fail-close.

Fail-close includes not-scoreable/stale snapshot, malformed/invalid/unsupported
schema, snapshot mismatch, tool violation, invalid geometry, timeout/API error/
retry exhaustion, information-boundary violation, resource budget, and sensitive
credential material.

**KNOWN LIMITATION.** Attempt records do not persist provider-returned reasoning
effort. Requested `medium` reasoning is frozen in manifest/config and inserted
into the provider request, but is not per-attempt returned telemetry.

## 8. Synthetic worked boundaries

### Normal boundary

At synthetic `12:00Z`, a scoreable snapshot is frozen after collection and
simulator progression. Quant/detector rows persist; one tool-free valid LLM
attempt is persisted before its `VALID` decision. Hybrids either agree and
retain quant geometry or record `NO_TRADE`. Eligible directional decisions gain
pending simulator records. The cycle finalizes `COMPLETE`.

### Fail-closed LLM boundary

At synthetic `12:15Z`, snapshot/quant/detector processing is scoreable, but
the LLM response fails strict JSON validation. The attempt and raw-output hash
persist, followed by a `FAIL_CLOSED` `MALFORMED_JSON` decision. Adapted
LLM/hybrids are `NO_TRADE`; absent an outer pipeline exception, the cycle still
finalizes `COMPLETE`.

## 9. First-four validation and limitations

The completed first-four read-only validator reported four exact prospective
boundaries, terminal `COMPLETE` cycle rows, fresh scoreable snapshots, one LLM
attempt per boundary, zero duplicate groups/open gaps/FK violations, one anchor
and one evidence window, active/enabled service with zero restarts, one
authoritative writer, matched Mullvad invocation/pass counts, healthy timer
executions in scope, and no validator failures. These are integrity/process
guarantees, not performance claims.

**KNOWN LIMITATION.** `tool_calls_count == 0` and recorded metadata demonstrate
absence of the currently instrumented external-tool leakage mechanism; this is
not exhaustive proof against every hypothetical leakage mechanism. Provider-
returned reasoning effort is not stored per attempt.
