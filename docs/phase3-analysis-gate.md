# Phase 3 analysis gate

This branch freezes the prospective Phase 3 analysis gate without reading or
modifying active research outcomes. The formal evaluator is pure: callers must
provide paired, already-attributed evidence, and open positions are represented
only as right-censored counts and durations. It has no database write path.

The operational telemetry reader opens SQLite in `mode=ro`, enables
`query_only`, and reports only collection/process health, retry and identity
telemetry, per-comparison co-eligibility counts, and API budget consumption. It
does not select paper returns, PnL, expectancy, or trade outcomes.

Co-eligibility is reconstructed separately for every frozen comparison from
snapshot timestamps and strategy position-active intervals. Suppressed signals
do not create active intervals. This produces early readiness telemetry without
materializing outcomes or reading any return field.

The frozen contract is `config/phase3/analysis_gate_v1.yaml`. Its actual
production use is prohibited before all preregistered calendar, sample, ESS,
regime, and trade-count gates are eligible. Tests use synthetic fixtures and a
reduced bootstrap repetition count; only synthetic fixtures may override the
frozen 10,000-resample setting.

Completion or readiness of downstream execution infrastructure is not an input
to the Phase 3 decision and must not create pressure to soften or accelerate the
frozen evidence gate.
