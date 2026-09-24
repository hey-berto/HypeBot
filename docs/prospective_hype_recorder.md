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

The separate-host launcher subscribes to HYPE trades, L2, and active context,
records funding through public read-only polling, and starts a distinct session
after reconnect. It still requires a seven-day VPS soak before any installation.

## Clock, lineage, and soak acceptance

Each session stores Git SHA, schema version, config identity, process-start UTC,
and reconnect lineage. `timedatectl` synchronization evidence is stored as an
immutable health event. `UNCERTAIN` clock health never rewrites prior receipt
times; future replay must qualify those intervals rather than correct them.

Install only on a separate Ubuntu host with `HYPE_RECORDER_DATABASE`,
`HYPE_RECORDER_GIT_SHA`, and `HYPE_RECORDER_CONFIG_ID`. During the seven-day
soak, measure rows/day, DB/WAL growth, L2 share, CPU/memory/I/O, stream rate,
lag/backlog, gaps, duplicates, out-of-order rows, and clock status. Execute one
controlled disconnect/reconnect, process restart, and daily rotation/integrity
check. `NATURAL_DISCONNECT_NOT_OBSERVED` is not a failure. Continue read-only
monitoring to day 30 after a provisional pass.

## Capacity and rotation

The configured `QueueConfig` is immutable per process start (initial default:
10,000 events; warning 70%; failure 90% or any overflow; sustained warning 60s).
Overflow is recorded as health evidence and the producer receives an explicit
non-durable result; it is never silent. Queue depth, maximum depth, warning
duration, recovery duration, and p50/p95/p99/max enqueue-to-durable latency are
reported. Set values in the separate-host launch configuration only after a
measured pre-soak load test, not automatically.

Daily sealing checkpoints WAL, copies a deterministic
`hype-raw-YYYY-MM-DD.sqlite3` archive, checks integrity, and records SHA-256.
Sealed archives are never deleted by recorder code. Operational resource
telemetry reports DB/WAL bytes and stream staleness; host CPU, memory and I/O
must be captured by systemd/cgroup or node exporter during soak.
