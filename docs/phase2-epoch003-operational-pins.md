# Phase 2 epoch003 production operational pins

Status: review/dry-run only. This operations branch is not the scored source
checkout. The prospective scored source remains detached, clean, and pinned to
63d2d4ecc1f135ae94e517164095b5b0a2c2ba5b on
codex/phase2-epoch003-strict-contract. No service start, scored grant, scored
DB creation/promotion, backfill, or Phase 3 evidence-clock start is authorized
by this document.

The production service template pins:

| Item | Epoch003 identity |
| --- | --- |
| Runtime source | 63d2d4ecc1f135ae94e517164095b5b0a2c2ba5b, detached /opt/hypebot/phase2 |
| Config | config/phase2/phase2_epoch_003.yaml; canonical hash 7afcddf8e939d742376d9823117f6652ad6dffe20bb6a66cd4979d84c0d7d955 |
| Prompt | c556b5d5f9ca7b9e4c6b7aaa11b40af137c7f98c22a20a2804db6373872e5f78 |
| Strict LLM_OUTPUT_V2 schema | 97318c27b3765780916efe010c3653fa8f8b097bdddd20ef711d40f41a5a1be4 |
| Database schema | 2ed4cb44c89833a06e3624103cac413723fcf2f1f234e57bac749adca3c9e129 |
| Epoch/experiment | phase2_epoch_003 only |
| Model/reasoning | gpt-5.6-terra / medium |
| Scored DB | /var/lib/hypebot/phase2/phase2_epoch_003.sqlite3 |
| Durable grant | /etc/hypebot/authorized/phase2-epoch-003.grant.json |

The separate root-owned operations checkout supplies
deploy/operations/phase2_epoch003_start_gate.py. Systemd ExecStartPre
performs a read-only, immutable SQLite check before the scored worker may
open its database. It requires the exact Git SHA, detached clean checkout,
config/prompt/schema hashes, model/reasoning, epoch ID, database integrity,
zero FK violations, exact live DDL, one immutable activation manifest, and a
root-owned mode-0640 durable grant (readable only by the dedicated
`hypebot-phase2-auth` group) matching that manifest. Missing DB/grant or any drift
returns nonzero. The grant and manifest are not created by startup.
ExecStart retains the independently reviewed supervisor/worker identity and
single-writer lease checks.

The same gate requires Mullvad Connected via a Singapore relay, configured
country sg, Lockdown mode on, no split-tunnel exclusions, no proxy environment,
and all resolved OpenAI IPv4 routes for the service UID through wg0-mullvad.
Routable IPv6 must also use that interface; unreachable IPv6 is acceptable.
The service address-family sandbox retains `AF_UNIX`, `AF_INET`, and `AF_INET6`
and additionally permits only `AF_NETLINK`, which is required by the gate's
read-only `ip route get` probe. An otherwise identical hardened-context A/B
test failed without `AF_NETLINK` and passed with it; no unrelated hardening or
network fallback was removed or added.
The periodic health service calls the network-only gate and alerts on failure.
The approved VPN may rotate among Singapore relays/IPs. It must not switch
countries or silently fall back to direct/proxy egress. Mullvad's host-level
Lockdown mode is an additional stop on internet access when disconnected.

The root-owned operational worker wrapper separately calls the same path
assertion immediately before *every* scored provider invocation. It appends a
fsync'd PASS or BLOCKED event to
`/var/log/hypebot/phase2-runtime-network.jsonl`; an invalid/unknown route or a
telemetry write failure raises a provider error before `urllib` is entered.
It never selects, changes, or reconnects a relay and it contains no direct or
proxy fallback. The normal runner records that provider error as a fail-closed
operational outcome, and the scheduler never backfills missed boundaries.

The checked-in historical epoch002 material in docs/ubuntu-migration-runbook.md
and its old backup example is archival; it must not be used for epoch003.
Any future epoch003 backup must explicitly name the epoch003 source/destination
and occur only after separate scored authorization.
deploy/tmpfiles.d/hypebot.conf and the generic alert unit contain no epoch002
pin and remain unchanged.

Because /etc/systemd/system, /opt/hypebot/phase2, and
/opt/hypebot/operations are root-owned, installation is a separate privileged
operation. Keep the scored service disabled and inactive while installing the
reviewed files and exact detached runtime checkout. Do not copy these templates
into production from a mutable/unreviewed operations checkout. After install,
repeat systemd-analyze verify on the installed units and run the gate as the
hypebot service UID; both must pass before any later scored activation.
The unit intentionally fails until a separately authorized epoch003 DB,
manifest and grant exist.

Phase 3's epoch003 42-day evidence clock begins only at the actual future
scored activation timestamp. The existing epoch002 Phase 3 gate is not reused;
no epoch002 evidence is pooled with epoch003 and no missed boundary is backfilled.
