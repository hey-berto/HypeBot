# V2 source-precedent audit

| V2 rule | Source precedent | Classification | Finding |
|---|---|---|---|
| Population / rejected exclusion | `phase2_external_red_team_technical_review_packet.md` §4; `research_cycles` schema | FROZEN_PRECEDENT | Rejected is terminal, non-scoreable; do not reinterpret. |
| Boundary sequencing | `phase2/pipeline.py:collect_reconstruct_and_score`; `simulation/engine.py:process_until` | FROZEN_PRECEDENT | Progression precedes snapshot/scoring/submission. |
| Both-flat co-eligibility | technical packet §5; `phase3/operational.py:_coeligibility_by_comparison` | FROZEN_PRECEDENT | Same snapshot, both flat; strict active interval convention is source-supported. |
| `NO_TRADE=0`; open/pending censor; suppressed zero/non-executed | technical packet §5; `phase2/evaluation.py:StrategyOutcome.attributed_return` | FROZEN_PRECEDENT | Exact qualitative handling exists. |
| Return unit = `return_pct` | `simulation/engine.py:151`; schema stores `return_pct`, `net_pnl`, `r_multiple` | PRINCIPLE_DERIVED_NEW_CONVENTION | Source stores alternatives; it does not nominate the evaluator unit. Claude’s R-multiple proposal is **not confirmed**. |
| Cost-adjusted formula | `phase3/gate.py`; `phase2/evaluation.py` | PRINCIPLE_DERIVED_NEW_CONVENTION | V1 has a required series but defines no formula; V2 proposes normalized API cost only because stored return_pct already includes simulator costs. |
| Full LLM cost per LLM-dependent comparison | runner/LLM record store per-invocation cost | PRINCIPLE_DERIVED_NEW_CONVENTION | No allocation rule exists. Claude’s full-cost proposal is **not confirmed**. |
| Completed-round-trip trigger | gate threshold names; `PaperTrade` statuses | PRINCIPLE_DERIVED_NEW_CONVENTION | No frozen definition of “triggered trade”. Claude’s proposal is **not confirmed**. |
| Regime bucket = combined | snapshot canonical regime; gate requires buckets | PRINCIPLE_DERIVED_NEW_CONVENTION | Labels exist but V1 does not map its bucket field. |
| One censor duration from snapshot to cutoff | gate field only | PRINCIPLE_DERIVED_NEW_CONVENTION | No duration cardinality/origin is frozen. |
| Time then hash order | snapshot uniqueness schema; ordered readers; sequence-dependent gate | FROZEN_PRECEDENT | Time-series order is source precedent; hash makes sorting total. |
| 10-place half-even | `hashing.py` | FROZEN_PRECEDENT | Canonical serialization quantizes to 1e-10. |

The V2 draft contains no unchosen `UNRESOLVED` behavior, but cost adjustment,
API allocation, trigger count, return unit, regime linkage, and censor duration
remain consequential **new conventions** pending independent acceptance.
