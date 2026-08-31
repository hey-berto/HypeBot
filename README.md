# HYPE Autopilot — Phase 1

HYPE Autopilot is a deterministic research and paper-simulation system for prospective HYPE experiments. It continuously records HYPE and BTC market data, constructs one causal information boundary every 15 minutes, evaluates frozen Quant baselines and an independent setup detector, and manages paper positions under a shared execution model.

**It is not a live trading bot. It contains no OpenAI/LLM calls, order-placement code, wallet integration, private-key requirement, or real-capital path.**

## Architecture

```text
Official Hyperliquid REST/WebSocket
        │
        ▼
append-only raw SQLite observations ── gap detection/recovery
        │
        ▼
causal HYPE + BTC features ── freshness/scoreability checks
        │
        ▼
immutable DecisionSnapshot + SHA-256 hash
        │
        ├── Quant Trend v1
        ├── Quant Mean-Reversion v1
        └── Setup Detector v1 (independent; never a gate)
                 │
                 ▼
       shared persisted paper simulator
```

The collector uses the official `hyperliquid-python-sdk` `Info` methods:

- `candles_snapshot` for HYPE 1m/5m/15m/1h/4h and BTC 15m/1h/4h;
- `funding_history` for completed HYPE funding observations;
- `meta_and_asset_ctxs` for mark, mid, oracle, funding, open interest, and day notional volume;
- `l2_snapshot` for BBO fallback;
- websocket `candle`, `bbo`, and `activeAssetCtx` subscriptions for continuous operation.

REST calls retry with bounded exponential backoff. The websocket supervisor reconnects, runs REST catch-up, detects timestamp gaps, and retries unresolved gaps. Raw corrections are append-only revisions; causal reads select the latest revision available by the build time. A late correction cannot alter an already persisted snapshot.

## Time, immutability, and no-lookahead

All timestamps are timezone-aware UTC. A snapshot at `T` uses completed source observations timestamped no later than `T`; future candles, funding, context, and revisions are excluded. Source and ingestion timestamps are stored separately.

Snapshot identity and logical `created_at` are fixed to the quarter-hour evidence boundary so identical frozen inputs rebuild identically. Actual operational start/completion timestamps are stored in `research_cycles` and health events. Canonical JSON:

- sorts keys and emits UTF-8 without insignificant whitespace;
- normalizes timestamps to microsecond ISO-8601 with `Z`;
- quantizes finite floats to 10 decimal places using decimal `ROUND_HALF_EVEN`;
- normalizes negative zero;
- excludes only `snapshot_hash` from the hash payload;
- rejects non-finite values from scoreable snapshots;
- hashes with SHA-256.

SQLite triggers forbid snapshot and epoch-metadata updates/deletes. Unique constraints prevent duplicate strategy/version scoring and duplicate soak/scored cycles.

## Warm-up versus prospective scoring

Raw data and cycles are explicitly labelled:

- `WARMUP`: historical indicator initialization only;
- `SOAK`: unscored operational validation;
- `SCORED_PROSPECTIVE`: enabled only after the user explicitly starts a frozen epoch.

Historical results are never treated as Epoch 1 evidence. `schedule` remains `SOAK` while no active epoch exists. This repository does not start Epoch 1 automatically.

## Frozen Epoch 1 definitions

All parameters live in `config/epoch_001.yaml`; do not tune them against HYPE history.

### Quant Trend v1

- prior 20 completed 1h Donchian high/low, held until the next 1h completion;
- completed 15m close must be strictly outside the threshold;
- ATR14 from completed 1h candles;
- initial stop 3× ATR from actual simulated entry;
- after each completed 1m bar, a 3× ATR chandelier may tighten but never loosen;
- no profit target; 48-hour TTL.

### Quant Mean-Reversion v1

- 168 completed hourly funding observations, minimum 72;
- SHORT at funding z-score ≥2 and RSI14 ≥70;
- LONG at funding z-score ≤−2 and RSI14 ≤30;
- 2× ATR initial stop;
- signal-time 1h EMA20 target, rejected both at signal and after latency if not profitable-side;
- 12-hour TTL.

### Regime v1

- trend `UP`: EMA20 > EMA50 and EMA20 > its value six hours earlier;
- trend `DOWN`: inverse conditions; otherwise `RANGE`;
- volatility: current 1h ATR14/price against the causal last 1,440 observations;
- `LOW` ≤25th percentile, `HIGH` ≥75th, otherwise `NORMAL`;
- minimum 720 ATR-percent observations or the snapshot fails closed.

Indicator conventions are transparent local implementations: EMA seeded with the first-period arithmetic mean; RSI14 uses the last 14 completed close changes; ATR14 is the arithmetic mean of 14 completed true ranges; 24h VWAP uses 96 completed 15m typical prices weighted by base volume.

## Shared simulator assumptions

- maximum one pending/open paper position per strategy stream;
- signal-to-entry latency: 3 seconds;
- without sub-minute data, entry uses the first completed 1m close after eligibility and records `ENTRY_FALLBACK_1M`;
- taker fee: 4.5 bps per side;
- slippage: 2.0 bps per side;
- stop, target, chandelier, funding, and TTL are evaluated from persisted 1m/hourly evidence;
- if stop and target touch the same 1m bar, stop wins and `INTRABAR_ORDER_AMBIGUOUS` is stored;
- funding is signed by direction and accrued once per completed observation;
- orders, trades, fills, every cost component, return, and R multiple are persisted;
- pending/open trades resume from `last_processed_at` after restart.

## Install and test

Python 3.12+ is required.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install '.[dev]'
pytest
```

## Commands

```bash
hype-autopilot init-db
hype-autopilot show-epoch
hype-autopilot collect --warmup
hype-autopilot collect
hype-autopilot build-snapshot --at 2026-08-30T14:15:00Z
hype-autopilot run-cycle --at 2026-08-30T14:15:00Z
hype-autopilot schedule
hype-autopilot status
hype-autopilot detector-report
hype-autopilot trace-trade <paper-trade-id>
hype-autopilot validate-db
```

`schedule` runs the reconnecting websocket collector and aligns cycles to UTC quarter-hours with a short ingestion grace. Stop it with Ctrl-C. It is the command for an unscored 24-hour soak test.

Operational soak milestones can be recorded as immutable, append-only evidence tied to the
current Git commit and frozen configuration hash:

```bash
hype-autopilot --db data/soak.sqlite3 record-experiment-event SOAK_VERSION_FROZEN \
  --experiment-id phase1-soak-2026-08-30 --details-json '{"note":"collection version frozen"}'
```

Only after formal approval should the user run:

```bash
hype-autopilot start-epoch
```

## 24-hour soak review

Run `schedule` against a dedicated database without an active epoch. After approximately 24 hours, `status` should show roughly 96 SOAK cycles, no unexplained `missed_soak_cycle_boundaries`, no open collection gaps, no duplicate strategy scores, and health events for reconnect/recovery or rejected snapshots. Missing/stale required data creates a hashed but non-scoreable snapshot with explicit reasons.

For an end-to-end evidence chain, select a `paper_trade_id` and run `trace-trade`; the output joins the paper lifecycle and fills to the strategy decision, immutable snapshot, and per-source cutoffs.

## SQLite inspection

```bash
sqlite3 data/hype_autopilot.sqlite3 '.tables'
sqlite3 data/hype_autopilot.sqlite3 'select status,count(*) from research_cycles group by status;'
sqlite3 data/hype_autopilot.sqlite3 'select snapshot_timestamp,snapshot_hash,scoreable from decision_snapshots;'
sqlite3 data/hype_autopilot.sqlite3 'select * from collection_gaps where status != "RECOVERED";'
```

## Detector diagnostics

`detector-report` returns trigger rate, capture of Quant Trend/MR signals, capture of profitable Quant paper trades, and missed profitable trades by regime/direction. The report is deliberately labelled `PHASE_1_PROVISIONAL_PROXY_DIAGNOSTICS_NOT_FINAL_LLM_GATING_PRECISION_RECALL`; Phase 1 has no LLM outcomes and cannot establish final gating precision/recall.

## Known limitations

- Hyperliquid REST candle snapshots expose only the latest 5,000 candles; configured requests stay inside that limit. Long uninterrupted 1m retention therefore depends on continuous collection or the official historical archive.
- REST-only collection cannot reconstruct asset-context/BBO states that were never observed; missing critical context fails closed.
- OHLC data cannot reconstruct intrabar event order, so ambiguous stop/target bars resolve adversely.
- Paper results normalize to one HYPE unit and omit leverage/liquidation complexity.
- The included scheduler is single-process. SQLite uniqueness and restart recovery protect evidence, but production HA orchestration is outside this research POC.
