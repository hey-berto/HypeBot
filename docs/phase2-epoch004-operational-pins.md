# Phase 2 epoch004 operational pin candidate

Status: **review-only, not installed**. The reviewed research source is
`b14f551e9fe395f2371ce294745845adc949cbeb`, detached and clean at
`/opt/hypebot/phase2` only after a separately authorized installation. The
operations checkout is separate at `/opt/hypebot/operations`. This candidate
does not authorize scored activation, DB/grant/receipt/manifest creation,
provider calls, or historical backfill.

| Pin | Current installed epoch003 | Proposed epoch004 |
| --- | --- | --- |
| Research source | `4f38eb3d8d05765ab17b42bf054f6106c4ef6c52` | `b14f551e9fe395f2371ce294745845adc949cbeb` |
| Config | `config/phase2/phase2_epoch_003.yaml` / `7afcddf8e939d742376d9823117f6652ad6dffe20bb6a66cd4979d84c0d7d955` | `config/phase2/phase2_epoch_004.yaml` / `8074bc4eb833779d7182d63b8a9545bbabf6e6ae2fcb70f1db194b3a1b4d5fd5` |
| Prompt hash | `c556b5d5f9ca7b9e4c6b7aaa11b40af137c7f98c22a20a2804db6373872e5f78` | unchanged |
| `LLM_OUTPUT_V2` schema hash | `97318c27b3765780916efe010c3653fa8f8b097bdddd20ef711d40f41a5a1be4` | unchanged |
| Database schema hash | `2ed4cb44c89833a06e3624103cac413723fcf2f1f234e57bac749adca3c9e129` | unchanged |
| Model / reasoning | `gpt-5.6-terra` / `medium` | unchanged |
| Epoch / experiment | `phase2_epoch_003` | `phase2_epoch_004` |
| Scored DB | `/var/lib/hypebot/phase2/phase2_epoch_003.sqlite3` | `/var/lib/hypebot/phase2/phase2_epoch_004.sqlite3` |
| Grant | `/etc/hypebot/authorized/phase2-epoch-003.grant.json` | `/etc/hypebot/authorized/phase2-epoch-004.grant.json` |
| Start gate | `phase2_epoch003_start_gate.py` | `phase2_epoch004_start_gate.py` |
| Per-call VPN wrapper | `phase2_epoch003_runtime_worker.py` | `phase2_epoch004_runtime_worker.py` |

The generic Phase 2 service and health-unit templates are changed to epoch004
only. The health timer, alert unit, tmpfiles rule, service identity
`hypebot:hypebot`, dedicated authorization group `hypebot-phase2-auth`, log
paths, and two shared lock/lease paths are unchanged. The lock paths must stay
shared: they prevent an epoch003 and epoch004 writer from coexisting. The
epoch003 gate and wrapper files are retained byte-for-byte at their installed
hashes; sealed epoch002/003 DBs and authorization artifacts are never reused.

`ExecStartPre` continues to fail closed unless the detached Git SHA, clean
tracked worktree, config/prompt/output-schema/DB-schema hashes, epoch/model/
reasoning identities, root-controlled grant and immutable manifest, DB
integrity/FK/DDL, and approved Mullvad Singapore route all agree. Missing
epoch004 DB or grant is an expected failure before later activation. The
root-owned worker wrapper invokes the same route assertion immediately before
each provider call; invalid/unknown path or failed telemetry blocks the call.
No direct route, proxy, relay-country or alternate-provider fallback was added.
The service retains `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK`
and all other hardening. Grant mode remains root-owned, dedicated-group-readable
`0640`, not world-readable.

The health timer is bootstrapped relative to timer activation with
`OnActiveSec=5min`, then repeats from the health service's last activation with
`OnUnitActiveSec=5min`. It intentionally does not use `OnBootSec`: systemd
immediately elapses an already-past boot-relative timer, which is not the
required five-minute post-activation bootstrap. This timer correction is
operational-only and does not change the health service or research runtime.

Installation is a later privileged operation, not part of this review task.
After independent review, an operator must first verify source and operations
remote SHAs, clean checkouts, stopped service/timer and absent effective writer;
then use interactive sudo to detach the two `/opt/hypebot` checkouts at their
approved SHAs, install the exact operations service/health templates and
epoch004 gate/wrapper files as root-owned artifacts, and run `systemctl
daemon-reload` plus `systemd-analyze verify`. Verify installed hashes against
the reviewed commit and leave service/timer disabled and inactive. Do not use
the old epoch003 grant, receipt, manifest or DB. Scored activation requires a
separate fresh authorization transaction and full prospective preflight.

Non-scored acceptance uses an isolated temporary source/config/DB/grant
fixture. It checks exact identity and wrong-identity failures, old-epoch grant
rejection, unchanged fixture-DB hash, Singapore/Mullvad Lockdown/split-tunnel/
proxy/route failures, and per-call provider blocking without fallback.
`systemd-analyze verify` is run on staged unit files; no production unit is
installed or started. Research-defining files are exactly those in the
already-reviewed source commit; this operations change touches only deploy
artifacts, documentation and tests.

At review preparation on 21 Sep 2026, the staged Phase 2 service SHA-256 was
`76d7e777d71e64f8c484696aa16a951a156b4d325c15af62149a9b108eb77676`,
and staged health service SHA-256 was
`17ab7830b6f9aff1bf9339b70322d589736ee25d1beef1020e9300e1732bbeb9`.
Systemd verification exited successfully with only the pre-existing unrelated
Phase 1 `StartLimitIntervalSec` warning. The isolated route-only gate observed
`wg0-mullvad`, Singapore, and a visible Singapore egress IP; no OpenAI API
request was made. The exact test count, gate/wrapper hashes and operations
commit are recorded in the Notion handoff after the final candidate is committed.
