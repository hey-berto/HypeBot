# V2 expanded discriminating golden-fixture plan

All rows are synthetic and must use an in-memory SQLite database built from the
checked-in schema. They contain no epoch006 data or outcomes. Fixtures must
exercise the future derivation surface without implementing it before freeze.

| ID | Synthetic input | Required V2 result | Plausible alternative that must fail |
|---|---|---|---|
| A — variable position/reference scale + API normalization | Two otherwise equal closed LLM trades: entry prices `$10` and `$20`, stored return `0.0100000000`, API cost `$0.02` each, implicit quantity `1 HYPE` | API-cost returns `0.0020000000` and `0.0010000000`; adjusted returns `0.0080000000` and `0.0090000000` | raw `$0.02` subtraction, snapshot-price denominator despite actual entries, or an invented variable quantity |
| B — API cost with `NO_TRADE` / no notional | Complete LLM `NO_TRADE`, snapshot close `$25/HYPE`, API cost `$0.05`, no paper trade/entry | unadjusted `0`; reference notional `$25 × 1 HYPE`; adjusted return `-0.0020000000` | zero API charge, null result, division by missing entry, or raw `$0.05` subtraction |
| C — near-cutoff entered/right-censored | Directional trade from an in-window decision enters one second before cutoff and remains OPEN at cutoff | triggered count increments by one; pair is right-censored; no paired return or cost-adjusted return | omit from triggered count because not CLOSED, or include MTM return |
| D — trigger alternatives | Four strategies respectively produce directional-only suppressed, submitted PENDING, entered OPEN, and entered CLOSED trades | counts are `0,0,1,1` | directional counts `1,1,1,1`; submitted counts `0,1,1,1`; closed-only counts `0,0,0,1` |
| E — `signal_time == boundary` | Existing interval candidate has `signal_time` exactly equal to evaluated boundary and no exit | it is not prior-active at that boundary; leg is flat for pre-decision co-eligibility | inclusive `signal_time <= boundary` marks it active |
| F — `exit_time == boundary` | Prior trade has `signal_time < boundary` and `exit_time` exactly equal to boundary | trade is flat at boundary | inclusive activity `exit_time >= boundary` marks it active |
| G — identical timestamp/hash tiebreak | Two synthetic complete snapshots share the same timestamp but hashes are `bbb…` and `aaa…`; insert `bbb…` first | ordered series is `aaa…`, then `bbb…` | insertion order, rowid order, or hash-descending order |
| H — partial regime missingness | Scoreable canonical snapshot has `trend=UP`, missing `volatility`, `combined=UP_NORMAL` | reject entire package | infer NORMAL from combined or exclude just the row |
| I — inconsistent combined regime | Scoreable snapshot has `trend=UP`, `volatility=HIGH`, `combined=DOWN_HIGH` | reject entire package | trust combined, recompute silently, or exclude row |
| J — SQLite float → Decimal → HALF_EVEN | Bind Python floats through a SQLite `REAL` column, read them back, then use values including `1.23456789005`, `1.23456789015`, and `-0.0` | use the actual returned Python float, `Decimal(str(value))`, then 1e-10 HALF_EVEN; canonical signed zero is `0.0000000000`; expected strings are derived from that real storage/read path | direct `Decimal(float)`, pre-SQL literal rounding, HALF_UP, or retaining `-0.0000000000` |
| K — full vs shared/pro-rata allocation | One `$0.03` LLM call feeds direct LLM, Hybrid Trend, and Hybrid MR at a `$30` no-entry reference notional | each standalone comparison receives API-cost return `0.0010000000`; explicit metadata says values are non-additive for system spend | split to `$0.01` each, zero Hybrid cost, or sum three charges and call `$0.09` total system spend |

## Additional required fixtures

| Fixture | Synthetic input | Required V2 result | Alternative that must fail |
|---|---|---|---|
| Return denominator/source behavior | Closed long with raw entry `$100`, configured slippage, fees/funding, and stored `entry_price`; compare stored `net_pnl / entry_price` | exactly stored `return_pct`; invariant `SIMULATOR_COSTS_ALREADY_EMBEDDED_IN_RETURN_PCT=true` | divide by margin/capital/raw entry or deduct simulator costs again |
| Suppressed no-entry cost | Terminal suppressed LLM-dependent decision, no entry, snapshot close `$40`, API cost `$0.04` | return `0`, cost return `0.0010000000` from reference one-HYPE notional | treat suppression as cost-free or fabricate entry notional |
| Missing/zero denominator | No-entry LLM row with snapshot close null, zero, negative, NaN, or infinity | reject package before division | substitute `1`, carry null, drop cost, or divide |
| Pair return unit | Treatment/control closed trades have distinct `return_pct`, `r_multiple`, and raw PnL | paired difference uses stored `return_pct` only | R-multiple or raw-PnL difference |
| Boundary stage order | Prior trade exits exactly at boundary and a new directional decision is submitted at that boundary | prior trade flat; new same-boundary signal is not prior-active | decision-before-progression or same-boundary active suppression |
| Full valid regime Cartesian cases | One scoreable row for each of the nine non-UNKNOWN combinations | all accepted; combined equals marginals joined by `_` | reject a valid bucket or accept UNKNOWN in scoreable evidence |
| Rejected/non-scoreable boundary | Rejected/non-scoreable row otherwise containing decisions/trades | absent from arrays and trigger counts | reinterpret as zero-return/no-trade |
| Censor duration cardinality | One co-eligible pair with both legs open at cutoff | exactly one duration: `cutoff - shared_snapshot_timestamp` | one duration per leg or entry-to-query-time duration |
| Post-cutoff contamination | Valid-looking row one microsecond after cutoff | reject package | ignore row or include it |

Every golden package must also prove manifest/package validation, deterministic
repeat reconstruction, aligned array lengths, no post-cutoff influence, and no
invocation of non-binding diagnostics. Fixture J must assert the platform's
actual SQLite return values before asserting the canonical decimal strings, so
it tests the real storage/conversion path rather than an idealized decimal
literal.
