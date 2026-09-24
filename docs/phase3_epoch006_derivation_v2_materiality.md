# V2 ambiguity and materiality table

| Decision | Chosen rule | Classification | Sample size | Differences/costs | ESS/robustness | Verdict impact |
|---|---|---|---|---|---|---|
| Population | scoreable prospective, inclusive window | FROZEN_PRECEDENT | Yes | Yes | Yes | Yes |
| Pair flatness / sequencing | progress then snapshot then decisions; strict active interval | FROZEN_PRECEDENT | Yes | Yes | Yes | Yes |
| Return unit | `return_pct` | PRINCIPLE_DERIVED_NEW_CONVENTION | No | Yes | Yes | Yes |
| Cost adjustment | difference less normalized treatment API plus normalized control API | PRINCIPLE_DERIVED_NEW_CONVENTION | No | Yes | No | Yes |
| API allocation | full invocation cost to each LLM-dependent treatment pair | PRINCIPLE_DERIVED_NEW_CONVENTION | No | Yes | No | Yes |
| Trigger count | closed round-trip only | PRINCIPLE_DERIVED_NEW_CONVENTION | Gate eligibility | No | No | Yes |
| Regime linkage | shared snapshot trend/volatility/combined | PRINCIPLE_DERIVED_NEW_CONVENTION | No | No | Yes | Yes |
| Censor duration | one pair duration, snapshot to cutoff | PRINCIPLE_DERIVED_NEW_CONVENTION | Reporting only | No | No | No direct gate effect |
| Ordering | timestamp then hash | FROZEN_PRECEDENT | No | No | Yes | Yes |
| Precision | 1e-10 half-even canonical JSON | FROZEN_PRECEDENT | No | Boundary cases | Reproducibility | Potentially |

All new conventions require independent review before they are frozen. None may
be optimized against epoch006 outcomes.
