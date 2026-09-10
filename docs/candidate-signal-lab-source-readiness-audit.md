# Candidate Signal Lab source-readiness audit

Audit timestamp: `2026-09-06T04:55:32.827962Z`
Scope: capability and coverage only; no feature matrix, forward returns, alpha
statistics, Stage-1 outcomes or active-epoch evidence were read or produced.

## Public API probe

The read-only Hyperliquid `/info` API was queried for the 43-day interval from
2026-07-25T04:44:59.999Z through 2026-09-06T04:44:59.999Z.

| Source | Observed coverage | Readiness conclusion |
| --- | --- | --- |
| HYPE 15m candles | 4,129 rows; 2026-07-25T04:30:00Z through 2026-09-06T04:44:59.999Z | Available for the requested window |
| BTC 15m candles | 4,129 rows; same endpoints | Available and timestamp-alignable |
| HYPE funding | 1,032 hourly rows; 2026-07-25T05:00:00.032Z through 2026-09-06T04:00:00.036Z | Available with documented pagination |
| HYPE asset context | `openInterest` present in a point-in-time response | Current only; not a historical series |
| HYPE recent trades | 10 rows spanning about five seconds | Insufficient for historical order-flow hypotheses |

The official API documentation limits candle snapshots to the most recent
5,000 candles. Time-range responses paginate at 500 elements. The 43-day 15m
candle query is therefore inside the documented candle limit, while funding
requires pagination.

## Official archive capability

Hyperliquid's official historical-data documentation identifies:

- `s3://hyperliquid-archive/asset_ctxs/[date].csv.lz4` for historical asset
  contexts, including the source needed to reconstruct open-interest history;
- `s3://hl-mainnet-node-data/node_fills_by_block` (and older
  `node_fills`/`node_trades`) for historical fills/trades;
- requester-pays transfer and an explicit warning that archive uploads can be
  delayed or incomplete.

Archive retrieval was not attempted because it can incur requester-pays cost
and needs separate data-access authorization. The Lab accepts no substitute:
the two open-interest hypotheses remain `DATA_INVALID` until complete archive
coverage is acquired and normalized, and the three order-flow hypotheses
remain `DATA_INVALID` until fill coverage and taker/aggressor-side semantics
are proven from the selected official format.

## Provenance contract for a future authorized import

An authorized source build must record the exact S3 object locators, retrieval
timestamp, object SHA-256 values, decompressor/parser versions, coverage range,
duplicate policy and UTC normalization. Trade-side interpretation must be
verified against the official schema before converting `B`/`A` to taker buy or
taker sell. Source intervals are start-inclusive and end-exclusive; no forward
fill is permitted.

Only after those checks may a separate authorization run `HYPE_BATCH_001`.
This audit does not authorize archive spending, historical screening, testnet
activity or production evidence access.

## Primary references

- Hyperliquid historical data: https://hyperliquid.gitbook.io/hyperliquid-docs/historical-data
- Hyperliquid info endpoint: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
- Hyperliquid perpetuals info endpoint: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/perpetuals
- Official Python SDK `Info`: https://github.com/hyperliquid-dex/hyperliquid-python-sdk/blob/master/hyperliquid/info.py
