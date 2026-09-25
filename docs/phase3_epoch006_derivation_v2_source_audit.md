# V2 revised source-precedent audit

This audit used repository source and synthetic/static artifacts only. It did
not open epoch006 databases, outcomes, returns, PnL, or comparative results.

| V2 rule | Authoritative source | Classification | Exact finding |
|---|---|---|---|
| Population / rejected exclusion | `storage/schema.py:45-50,102-106`; frozen technical protocol | FROZEN_PRECEDENT | Snapshot timestamp, observation class, and scoreability are immutable population keys; rejected/non-scoreable boundaries are excluded and never reinterpreted. |
| Runtime stage order | `phase2/pipeline.py:117-131,133-165` | FROZEN_CODE_WITH_EVIDENCE_LIMITATION | `process_until(boundary)` precedes snapshot build; decisions precede `submit`. Persisted rows evidence the products, but no stage-level event ledger independently proves this ordering. |
| Active interval and co-eligibility | `phase3/operational.py:321-392` | FROZEN_PRECEDENT | Existing reconstruction uses `start < boundary` and `(end is NULL or end > boundary)`. Signal exactly at boundary is not prior-active; exit exactly at boundary is flat. |
| `signal_time` provenance | `simulation/engine.py:23-45`; `quant_trend_v1.py:24-31`; `quant_mean_reversion_v1.py:29-36`; `phase2/runner.py:734-763`; `phase2/hybrid.py:36-57` | SOURCE_DERIVED | Submit copies decision `created_at`. Quant uses snapshot boundary; LLM uses request end; Hybrid uses `max(quant.created_at, llm.request_ended_at)`. It is not uniformly the logical boundary or write time. |
| Return denominator | `simulation/engine.py:130-152`; `simulation/models.py:25-50` | SOURCE_DERIVED | Live `return_pct = net / trade.entry_price`. No quantity/margin/capital field exists; PnL is computed directly from per-HYPE price differences. Denominator is `entry_price USD/HYPE × implicit 1 HYPE`, i.e. one-HYPE entry notional. |
| Simulator costs already embedded | `simulation/engine.py:69-92,130-152` | SOURCE_DERIVED | Entry/exit fees, funding, and slippage enter `net`; `SIMULATOR_COSTS_ALREADY_EMBEDDED_IN_RETURN_PCT=true`. Live entry slippage affects slipped `entry_price` in gross and is also subtracted in `total_slippage`; V2 preserves stored return and never deducts these costs again. |
| `NO_TRADE=0`; pending/open censor; suppressed zero | `phase2/evaluation.py:26-60,93-136` | FROZEN_PRECEDENT | Qualitative outcome handling is source-defined. Pending/open has no realized numeric return; no-trade and terminal suppressed no-entry outcomes are zero. |
| Cost-adjusted algebra | `gate.py:56-78,212-276` defines required aligned series but no derivation; simulator source above establishes one-HYPE denominator | PRINCIPLE_DERIVED_NEW_CONVENTION | V2 converts USD API cost to dimensionless return using actual one-HYPE entry notional, or a one-HYPE snapshot reference notional only when no entry exists. Missing/non-positive denominators reject. |
| API cost source/allocation | `phase2/models.py:244-278`; `phase2/runner.py:577-622`; `phase2/hybrid.py:18-57` | SOURCE_FIELD_PLUS_NEW_ALLOCATION_CONVENTION | One `llm_decisions.model_cost_usd` belongs to the shared snapshot call. Full cost per LLM-dependent comparison answers standalone architecture economics; comparison costs are non-additive system-spend estimates. |
| Triggered-trade count | `simulation/models.py:16-22,25-50`; `simulation/engine.py:48-93`; `gate.py:51,81-87,245-257` | PRINCIPLE_DERIVED_NEW_CONVENTION | Gate names but does not define “triggered.” V2 counts actual entries (`entry_time` non-null; OPEN/CLOSED), so genuine near-cutoff entries count while return censoring remains separate. |
| Regime domains/derivation | `regimes/models.py:6-25`; `regimes/classifier.py:18-39`; `snapshots/builder.py:50,65-75,89-95` | SOURCE_DERIVED_VALIDATION_CONVENTION | Trend has 4 values, volatility 4; classifier derives `combined=f"{trend}_{volatility}"`. Scoreable snapshots reject UNKNOWN marginals. Missing/conflicting included labels reject the package. |
| Right-censor duration | Gate field only; `phase2/evaluation.py:106-112` supplies status semantics | PRINCIPLE_DERIVED_NEW_CONVENTION | One duration per co-eligible censored pair, from shared snapshot timestamp to formal cutoff; no MTM. |
| Series order / hash identity | `storage/schema.py:45-50`; `snapshots/canonicalize.py:7-19`; ordered readers such as `phase3/nonbinding_diagnostics.py:48` | FROZEN_PRECEDENT_WITH_EXPLICIT_TOTAL_ORDER | Sort timestamp then existing immutable Phase 2 snapshot hash. Timestamp uniqueness usually prevents ties, but hash defines deterministic total order. |
| SQLite float conversion | `storage/schema.py:70-79`; `data/repository.py:290-315`; `hashing.py:13-20,34-35,54-55` | FROZEN_PROJECT_CONVENTION | REAL values are bound/read as Python floats; canonical conversion is `Decimal(str(value))`, quantized to `1e-10` with HALF_EVEN. Direct binary-float Decimal construction is prohibited. |

## Trigger-count alternative assessment

| Alternative | Opportunity relation | Realized-evidence relation | Right-censor / cutoff effect | Deterministic? | Decision |
|---|---|---|---|---|---|
| Directional decision | intent generated | no execution required | counts suppressed/unfilled intent | yes, decision row | reject |
| Submitted/pending | order opportunity accepted | no fill required | counts pending at cutoff | yes, trade/order row | reject |
| Actual entry | genuine opportunity executed | return may be unrealized | entered-open counts; return remains censored | yes, `entry_time` + status | **select** |
| Completed round trip | executed and realized | strongest realized evidence | drops genuine open entries near cutoff | yes, CLOSED | reject |

## Consequential unresolved fields

None. The revised choices remain unfrozen pending independent review, but no
required derivation field is left `UNRESOLVED`. Adapter implementation remains
prohibited until the exact bytes and revised hash pass freeze review.
