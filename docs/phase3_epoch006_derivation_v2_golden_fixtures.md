# V2 discriminating golden fixtures — specification only

All fixtures are synthetic and contain no epoch006 outcomes. Each must be
implemented before an adapter is written.

| Fixture | Synthetic rows | Required V2 result | Alternative that must fail |
|---|---|---|---|
| Return-unit discriminator | One closed high-risk and one closed low-risk trade with equal `return_pct` but different `r_multiple`/`net_pnl` | paired value equals return-pct difference | R-multiple or raw PnL selection |
| API allocation | One LLM call feeding direct LLM and Hybrid pair, shared snapshot price | full normalized cost appears in each respective LLM-dependent treatment comparison | pro-rata, zero Hybrid cost, or aggregate-portfolio allocation |
| Trigger count | Directional suppressed, pending, open, and closed decisions | only closed increments its strategy count | directional/submitted/entry count |
| Ordering | Four completed snapshots inserted out of order | timestamp then hash order feeds paired arrays | insertion/row-id order |
| Boundary sequencing | Prior trade exits exactly at boundary plus a new decision | prior leg is flat for that boundary | decision-before-progression suppression |
| Regime mapping | Snapshot with distinct trend, volatility, combined labels | three specified labels fill GateEvidence arrays | strategy-specific or arbitrary bucket |
| Censor duration | One pending and one open co-eligible pair | one duration each from snapshot to formal cutoff | entry-to-query time, one-per-leg |
| Rejected boundary | Rejected/non-scoreable row with otherwise valid decisions | absent from all arrays/counts | V2 reclassification as no-trade |
| Precision edge | value at 11th decimal half boundary and negative zero | 10-place half-even/canonical zero | binary-float or half-up serialization |

Every golden package must also prove manifest/package validation, no
post-cutoff influence, deterministic repeated reconstruction, and no invocation
of non-binding diagnostics. The same synthetic frozen package must be used for
formal reconstruction and the separate shadow diagnostic, with the formal
result unchanged.
