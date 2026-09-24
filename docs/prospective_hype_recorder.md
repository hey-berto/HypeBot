# Prospective HYPE recorder

**FUTURE_RESEARCH_DATA — NOT EPOCH006 EVIDENCE.** This separate-host recorder
has no wallet, private-key, order, or epoch006 database integration.

## Architecture

The append-only SQLite core records exact raw payloads with SHA-256, source
timestamp when supplied, UTC receipt time, monotonic receipt time, stream,
session, source key, and out-of-order flag. Connection and gap tables preserve
disconnect/reconnect/subscription-restoration evidence. Raw rows are immutable;
later normalized research views must be derived from them, never overwrite them.

Required HYPE adapters: `trades`, `l2Book`, `activeAssetCtx`, and periodic
`fundingHistory` polling. The WebSocket adapter must create a session at start,
subscribe each stream after every reconnect, open stream-specific gaps on loss,
and close them only after the restored subscription receives data. It must not
backfill gaps silently. BTC is deliberately excluded from v1; add it only after
HYPE storage/health is stable.

`trades` and `l2Book` have event timestamps in their native payloads. The
current active-asset-context callback used elsewhere in this repository does
not expose a source timestamp, so this recorder records `source_timestamp=NULL`
there rather than inventing one. Funding records settlement timestamps; receipt
time records when the poll actually observed them.

## Future causal replay

Raw trades can deterministically form 1m/5m/15m/1h/4h candles, but raw events
remain authoritative. Replay at boundary T selects only raw rows with
`received_at <= T`, preserves recorded gaps, and must prove future-mutation
invariance. The frozen simulator can then reconstruct pending/open/suppressed,
latency, TTL, stop/target (stop wins same-bar ambiguity), and lock state without
exporting PnL or returns.

## Separate-host deployment proposal

Ubuntu 24.04 LTS, 2 vCPU, 4 GB RAM, and 100 GB SSD is sufficient to begin; use
200 GB for a year if dense L2 is retained. SQLite WAL is suitable initially with
daily database rotation and monthly sealed read-only archives. Use a dedicated
`hype-recorder` user, `/var/lib/hype-recorder`, NTP/chrony, systemd restart,
and backups of sealed daily DB plus SHA manifest. The included service template
is a proposal only and is not installed anywhere. Health is `PRAGMA
integrity_check`, open-gap count, and latest-receipt age.

Storage is highly L2-rate dependent: trades/context/funding are expected to be
small relative to L2. Estimate and set retention only after a seven-day
prospective measurement; if full L2 exceeds ~5 GB/day, rotate compressed raw
JSONL/LZ4 daily while keeping SQLite indexes/metadata, rather than adopting a
distributed system.

## Remaining implementation gate

The storage core and test harness are complete. A production network launcher
must be implemented and soak-tested on the separate VPS before installation;
the service template intentionally names that future launcher and is not
deployable as-is.
