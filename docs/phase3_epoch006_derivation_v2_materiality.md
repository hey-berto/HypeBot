# V2 revised ambiguity and materiality table

`Yes` means the rule can change the named quantity for at least one valid
synthetic input; it does not assert anything about epoch006 outcomes.

| Decision | Frozen/revised rule | Classification | Sample size | Triggered minimum | Paired return | Cost-adjusted return | ESS | Regime robustness | PROMOTE / REJECT / INCONCLUSIVE |
|---|---|---|---|---|---|---|---|---|---|
| Population/window | scoreable prospective, inclusive endpoints; post-cutoff rejects | FROZEN_PRECEDENT | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Active interval | `signal < b`, `exit NULL or > b`; exact signal/exit boundary cases explicit | FROZEN_PRECEDENT | Yes | No | Yes | Yes | Yes | Yes | Yes |
| Runtime stage order | progress → snapshot → decisions → submission; code-trusted limitation disclosed | FROZEN_CODE_WITH_EVIDENCE_LIMITATION | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Return denominator | stored `net / entry_price`, implicit one-HYPE entry notional | SOURCE_DERIVED | No | No | Yes | Yes | Yes | Yes | Yes |
| Embedded simulator costs | use stored return; never deduct fees/funding/slippage again | SOURCE_DERIVED | No | No | Yes | Yes | Yes | Yes | Yes |
| API-cost denominator | actual one-HYPE entry notional; snapshot one-HYPE reference only for no-entry completion | PRINCIPLE_DERIVED_NEW_CONVENTION | No | No | No | Yes | No (ESS uses unadjusted paired series) | No (robustness uses unadjusted paired series) | Yes |
| API allocation objective | full shared call charged to each standalone LLM-dependent architecture comparison | PRINCIPLE_DERIVED_NEW_CONVENTION | No | No | No | Yes | No | No | Yes |
| Trigger count | actual executed entry, OPEN or CLOSED, by cutoff | PRINCIPLE_DERIVED_NEW_CONVENTION | No | Yes | No | No | No | No | Yes |
| Right censoring | open/pending pair omitted from numeric series; one snapshot-to-cutoff duration | PRINCIPLE_DERIVED_NEW_CONVENTION | Yes | No (actual entered-open still counts trigger) | Yes | Yes | Yes | Yes | Yes |
| Regime domains/consistency | nine non-UNKNOWN scoreable buckets; missing/conflict rejects package | SOURCE_DERIVED_VALIDATION_CONVENTION | Potential reject | No | No | No | No | Yes | Yes |
| Ordering/tiebreak | ascending timestamp then immutable Phase 2 snapshot hash | FROZEN_PRECEDENT_WITH_EXPLICIT_TOTAL_ORDER | No | No | Sequence only | Sequence only | Yes | No | Yes |
| Float canonicalization | SQLite REAL → Python float → `Decimal(str(value))` → HALF_EVEN 1e-10 | FROZEN_PROJECT_CONVENTION | Potential reject | Boundary cases | Boundary cases | Boundary cases | Boundary cases | Boundary cases | Potentially |
| Exposure disclosure | prior operational/sample information and “3/17” stated plainly | DISCLOSURE | No | No | No | No | No | No | Review validity only |

The formal gate computes ESS, bootstrap/CI, split-half, best-removal, and regime
robustness from the unadjusted paired-return series; API cost affects the
separate `positive_after_api_cost` robustness condition. Triggered counts affect
minimum eligibility, and therefore can change only the resulting
`INCONCLUSIVE` versus evaluable path, not an individual paired value.
