# HYPE Autopilot Ubuntu migration runbook

Status: preparation only. These commands are **not authorized for active
cutover**. Do not create an authorization sentinel, enable a service, stop the
Mac writer or transfer the live database until a separate cutover instruction.

## Operational integration candidate — 10 Sep 2026

The review branch `codex/phase2-ubuntu-operational-integration` reconciles the
exact Phase 2 recovery commit `33d410bd9e9da2401eef357a635349658982b7d7`
and Phase 3 metadata commit `3b478af758940e53a102fca4a0c43c87d81dddb5`
without rebasing either identity. It adds repository-owned, parameterized
Phase 2 worker and single-writer supervisor entrypoints. The production unit
remains an uninstalled `.template` and pins the independently reviewed source
identity `0a9eb262bb77980106cbfad03336dd4209a34308` in both `ExecStartPre` and
the supervisor command. The runtime-identity preflight and worker runtime both
fail closed if the actual checkout differs. This pin does not authorize
installation, activation or cutover.

Runtime dependencies for Ubuntu 24.04 / CPython 3.12 / x86_64 are frozen in
`requirements/ubuntu-x86_64-py312.lock`. The lock was selected and downloaded
on target host `mmt2`, matches the material versions in the Mac runtime, uses
wheel artifacts only, and requires SHA-256 verification:

```bash
python3 -m venv /opt/hypebot/phase2/.venv
/opt/hypebot/phase2/.venv/bin/pip install --require-hashes \
  -r /opt/hypebot/phase2/requirements/ubuntu-x86_64-py312.lock
/opt/hypebot/phase2/.venv/bin/pip install --no-deps /opt/hypebot/phase2
```

The second command installs only the reviewed local project after every third-
party dependency has passed the hash gate. Do not use an unlocked `pip install
.` for the service environment.

Phase 2 mutable state may be outside the source checkout only when the worker
is given the exact `--data-root`; path resolution rejects symlink escapes,
sidecars, non-`.sqlite3` files, and any Phase 1 path. Kernel writer and
supervisor leases live under `/run/lock/hypebot`, created by the committed
tmpfiles rule. The worker has no Mac path and no `caffeinate` dependency.

Activation is still a separate future action. A root-controlled, mode-0600,
single-use receipt is converted once by `hype-autopilot-phase2-authorize` into
a phrase-free durable grant; the original receipt is atomically renamed with a
`.consumed` suffix. Service startup only reads a durable grant and verifies it
against the immutable manifest already in the database. It cannot manufacture
authorization. Do not create either file during non-scored acceptance.

Operational monitoring uses `hype-autopilot-tooling phase2-health`. Its output
is explicitly limited to process, boundary freshness, gaps, duplicates,
integrity and FK status—never PnL, returns, win rate, or strategy performance.
The five-minute timer and external `OnFailure` hook remain uninstalled review
templates. The repository-owned `hype-autopilot-alert` adapter accepts only a
small allowlist of fatal infrastructure classifications, emits a fixed
performance-free payload, appends a local audit row, sends to the HTTPS webhook
in `HYPEBOT_ALERT_WEBHOOK_URL`, and suppresses repeated component/classification
notifications for 15 minutes. An optional bearer token is read from the
root-controlled environment and is never persisted or printed. Missing or
failed external delivery fails the alert unit; it can never block or mutate the
research worker because it runs only in the separate `OnFailure` service.
The alert unit executes from the separately reviewed
`/opt/hypebot/operations` checkout, so the Phase 2 research runtime may remain
detached at the exact approved `0a9eb262bb77980106cbfad03336dd4209a34308`
source pin while operational-gate tooling follows its own reviewed commit.

Consistent backups use SQLite's backup API and fail if the destination exists:

```bash
hype-autopilot-tooling sqlite-backup \
  --source /var/lib/hypebot/phase2/phase2_epoch_002.sqlite3 \
  --destination /var/lib/hypebot/phase2/backups/phase2_epoch_002.TIMESTAMP.sqlite3 \
  --manifest /var/lib/hypebot/phase2/backups/phase2_epoch_002.TIMESTAMP.json
hype-autopilot-tooling sqlite-verify-backup \
  --backup /var/lib/hypebot/phase2/backups/phase2_epoch_002.TIMESTAMP.sqlite3 \
  --sha256 SHA256_FROM_MANIFEST
```

A restore is never performed over an existing database. Stop and prove all
writers absent, verify the backup hash/integrity/FKs, copy to a new staging
name, verify again, reconcile critical counts and latest boundary, and only
then atomically rename while the destination is absent. Starting a restored
writer remains a separately authorized cutover action.

## Current-host audit — 6 Sep 2026

The read-only identity audit found:

- Phase 1: clean `main` at
  `e4305c35fd4e73a23ffab83bdf1fa1502e24709c`; canonical epoch config hash
  `d55e10d5ff56308463db0fff5086ad1efbd6cebc9b545993998c2eb8fc745851`;
  DB integrity `ok`, zero FK violations, schema hash
  `46f02feacbaced6d3ca95e799e292d7573608b37f183f3929722fef938acc7ab`.
- Phase 2: clean `codex/phase2-build` at
  `0322e53ba55df1c9ad44286616cd71bd2e0700c6`; config file SHA-256
  `b40eeb771ae15cd007bbc9c39da850f55befef6614595862c78e2184cff08c5b`;
  DB integrity `ok`, zero FK violations, schema hash
  `2c5920aa1e3c3749dca79c971ce14ff8f818793803ba3f60b9a18b1b57d03b1`.
- Inspection environment: Python 3.12.14, SQLite 3.53.1,
  `hyperliquid-python-sdk` 0.24.0, Pydantic 2.13.5, NumPy 2.5.2, Arch 8.0.0.
- Phase 1 launchd supervisor PID 868 is alive, but no scheduler child is
  running. Since 2026-09-06T09:52:25Z the supervisor has failed its setup loop
  because macOS returns `Operation not permitted` when it tries to read the
  repository working directory. This is an active operational incident, not a
  migration test, and was not repaired under this preparation-only task.
- No Phase 2 supervisor/worker is loaded. Its frozen production DB remains
  stopped. The approved external supervisor and worker exist outside Git with
  SHA-256 values `598d329e...ccb0` and `8fe30d47...ca5e`, matching the V2
  activation manifest, but both hard-code Mac paths and the supervisor invokes
  `/usr/bin/caffeinate`.

The last item is a migration blocker: the Phase 2 external runtime artifacts
must be parameterized for Ubuntu, reviewed, tested non-scored and assigned new
immutable operational hashes. The invalid placeholder in the Phase 2 systemd
template prevents accidental installation before that work is complete.

## Target and layout

Target Ubuntu 24.04 LTS with system time set to UTC and NTP synchronized.

```plain text
/opt/hypebot/repo             # authoritative Git clone
/opt/hypebot/phase1           # immutable Phase 1 worktree
/opt/hypebot/phase2           # immutable Phase 2 worktree
/opt/hypebot/phase3           # tooling/review worktree
/opt/hypebot/operations       # reviewed operational-gate/alert tooling
/opt/hypebot/*/.venv          # per-worktree Python environment
/var/lib/hypebot/phase1       # mutable Phase 1 evidence
/var/lib/hypebot/phase2       # mutable Phase 2 evidence
/var/log/hypebot              # persistent logs
/etc/hypebot                  # root-owned environment files
/etc/hypebot/authorized       # explicit cutover sentinels; initially empty
```

Create a non-login `hypebot` service account. Code is root-owned and read-only
to that account; the service account owns only `/var/lib/hypebot` and
`/var/log/hypebot`. `/etc/hypebot/*.env` must be root-owned mode 0600. Secrets
never enter Git, shell history, process arguments, review bundles or logs.

## User setup (safe before cutover)

```bash
sudo apt-get update
sudo apt-get install -y git python3.12 python3.12-venv sqlite3 curl ca-certificates
timedatectl status
systemctl status systemd-timesyncd
sudo useradd --system --home /var/lib/hypebot --shell /usr/sbin/nologin hypebot
sudo install -d -o root -g root -m 0755 /opt/hypebot /etc/hypebot /etc/hypebot/authorized
sudo install -d -o hypebot -g hypebot -m 0750 /var/lib/hypebot/phase1 /var/lib/hypebot/phase2 /var/log/hypebot
sudo git clone https://github.com/hey-berto/HypeBot.git /opt/hypebot/repo
sudo git -C /opt/hypebot/repo worktree add --detach /opt/hypebot/phase1 e4305c35fd4e73a23ffab83bdf1fa1502e24709c
sudo git -C /opt/hypebot/repo worktree add --detach /opt/hypebot/phase2 0322e53ba55df1c9ad44286616cd71bd2e0700c6
sudo git -C /opt/hypebot/repo worktree add --detach /opt/hypebot/phase3 __PHASE3_TOOLING_COMMIT__
test "$(git -C /opt/hypebot/phase1 rev-parse HEAD)" = e4305c35fd4e73a23ffab83bdf1fa1502e24709c
test "$(git -C /opt/hypebot/phase2 rev-parse HEAD)" = 0322e53ba55df1c9ad44286616cd71bd2e0700c6
```

Create separate virtual environments with Python 3.12 and install each exact
checkout using its committed project metadata. Record `python --version`,
`sqlite3 --version`, and `pip freeze --all`. Do not upgrade or regenerate a
lock file during migration. A version that cannot install is a blocker.

Read-only connectivity checks may use:

```bash
curl --fail --silent --show-error https://api.hyperliquid.xyz/info \
  -H 'Content-Type: application/json' --data '{"type":"meta"}' >/dev/null
curl --fail --silent --show-error https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY" >/dev/null
```

Run the second command only from a protected shell with the key already
injected. Never echo it.

## Runtime identity gate

Use `hype-autopilot-tooling runtime-identity` exactly as shown in the systemd
templates. It validates the exact Git SHA, tracked-worktree cleanliness,
config SHA-256, read-only SQLite integrity/FK/schema identity, Python, SQLite
and material package versions. It opens databases with
`mode=ro&immutable=1`; it cannot repair or modify them.

Before cutover, replace the Mac-only absolute paths and `caffeinate` dependency
in the external Phase 2 supervisor/worker through a separately reviewed
operational-only change. The source artifacts and hashes are recorded in
`config/migration/runtime_inventory.yaml`. The Phase 2 systemd template is
explicitly pinned but remains inactive until Ubuntu artifacts pass every
non-scored gate and a separate cutover authorization is issued.

## Mac ↔ Ubuntu deterministic replay gate

On each host, from the exact same Phase 1 checkout/config and tooling commit:

```bash
hype-autopilot-tooling platform-replay \
  --root /opt/hypebot/phase1 \
  --fixture /opt/hypebot/phase3/config/migration/platform_replay_fixture_v1.yaml \
  --output /tmp/hype-platform-replay.json
sha256sum /tmp/hype-platform-replay.json
```

Compare the entire JSON files with `cmp`, not just selected fields. Canonical
raw-input hash, normalized HYPE/BTC features, regime, both Quant decisions,
detector output, canonical snapshot JSON, `snapshot_hash` and overall replay
hash must be byte-identical. Do not normalize away a mismatch. Record Python,
SQLite and package versions and root-cause any discrepancy.

The current Mac reference run completed twice byte-identically:

- fixture hash: `12032c4864c43d575caa9253fdef8905bcc6cbc7dac7d2d2caa39538a533a4bc`
- raw-input hash: `fa7f082c80e8aaf94d66d53a46b462dc6a9a4a3e07aaa3aa7b6a84d6161d3f2c`
- snapshot hash: `e46094d11450c2d4343cb6fdaeabc51a0d40205eab1ceba60c5c2a74a5d63bdb`
- replay hash: `02213957b1c723cd4eed28a033d66e04b68fff6710ce3f225b14811e10cc994c`
- serialized file SHA-256: `f34561b1d5782a4b127b33e3c87c31f4cdd4b1097b1b311f0b950e49852c9580`

This is the Mac half of the gate only. Ubuntu parity remains unexecuted.

The current simulator uses a 3-second signal-to-entry latency. Measure
snapshot-to-provider and provider-to-persist latency on Ubuntu before deciding
whether a host move remains operational-only. If a scored timing input or
entry-price selection changes, create a new prospective experiment identity;
do not pool silently into the old epoch.

## Mandatory Ubuntu non-scored soak gate

Before any scored cutover review, run the externally supervised non-scored
soak for **at least 24 consecutive scheduled boundaries and at least 6
continuous hours**. Both lower bounds must pass; the longer effective threshold
governs. The committed soak harness refuses a boundary count below 24 and does
not complete before six hours have elapsed.

Acceptance requires all of the following, with no waiver or shortened run:

- zero duplicate cycle, snapshot, strategy/detector decision, LLM decision,
  LLM invocation-attempt/raw-response, paper-trade, order or fill keys;
- zero missing quarter-hour boundaries unless each is covered by an explicit,
  immutable recovery event;
- zero foreign-key violations and SQLite `quick_check` plus `integrity_check`
  equal to `ok`;
- exactly one effective writer for the entire run, with supervisor and scheduler
  health continuously valid;
- every applicable completed LLM result validates as `LLM_OUTPUT_V2`, every raw
  response is retained, and every recomputed SHA-256 equals its stored hash;
- retries and WebSocket reconnects remain inside the pinned bounded policies;
- no unexplained process death, restart loop, historical backfill, or scored
  evidence capability.

The present task prepares and tests this policy only. It does not run or
authorize the six-hour soak and it never uses the production epoch database.

## Authorized cutover procedure (future)

1. Select and record a future quarter-hour boundary and both host identities.
2. Stop the old Mac supervisor/worker and prove both are absent with
   `launchctl print`, `pgrep -af` and two independent checks. Record stop time.
3. Confirm no process has the DB open (`lsof`) and run pre-backup integrity/FK
   checks in read-only mode.
4. Use SQLite's online backup command against the stopped source database:

   ```bash
   sqlite3 /absolute/source.sqlite3 ".backup '/absolute/staging/epoch.backup.sqlite3'"
   shasum -a 256 /absolute/staging/epoch.backup.sqlite3
   ```

5. Transfer through authenticated SSH/SFTP to a temporary Ubuntu path, verify
   SHA-256, then atomically rename into `/var/lib/hypebot/<phase>/` while no
   service is running.
6. Re-run integrity/FK checks and reconcile exact critical-table row counts,
   maximum scheduled boundary, duplicate keys and active epoch identity against
   the recorded source inventory. Preserve all timestamps; backfill nothing.
7. Create only the specifically authorized sentinel, install the reviewed
   service unit, run `systemd-analyze verify`, then `systemctl enable --now`.
8. Prove one supervisor and one intended worker, no Mac writer, no duplicate or
   open gap, and validate the first four consecutive prospective boundaries.

The transfer artifact hash, old-writer stop timestamp, Ubuntu start timestamp,
first new boundary and any explicit gap become an immutable operational event.

## Scored-cutover rollback gate

Rollback is mandatory if any of these triggers occurs after a future authorized
cutover: an unexpected duplicate writer; Git/config/database-schema identity
mismatch; SQLite integrity or FK failure; an unrecoverable scheduled-boundary
gap; supervisor restart loop or circuit-breaker trip; invalid or unverifiable
raw-response audit chain; systematic `LLM_OUTPUT_V2` contract failures;
evidence-clock/pre-activation/backfill violation; or any unexpected mutation of
historical scored evidence. A single contained provider transport failure is
not by itself a rollback trigger when the frozen retry and rejection path works.

Required order—never reverse it:

1. Fail closed and stop the Ubuntu service. Record the trigger and UTC stop
   timestamp without modifying historical rows.
2. Prove the Ubuntu supervisor and worker are absent using `systemctl`, PID/
   process-start/process-group evidence, writer-lock acquisition, and two
   independent process checks. Do not proceed while any writer or DB holder
   remains.
3. Preserve the Ubuntu DB, WAL/SHM if present, journal, alert audit and runtime
   identity as immutable incident evidence. Create an online backup only after
   the writer is proven stopped; hash it and verify integrity/FKs. Never repair,
   overwrite, or reuse the pre-cutover source backup.
4. Determine whether same-epoch continuation is scientifically valid. Any
   historical mutation, evidence-clock violation, ambiguous dual-writer window,
   or research-identity change requires a new review and may require a new epoch;
   it must not be hidden by a restart.
5. Only after a separately approved rollback authorization may a same-epoch
   recovery artifact be transferred through the hash/integrity/count
   reconciliation procedure to the Mac. Keep the Mac service disabled until the
   Ubuntu writer-death proof is attached to the incident record.
6. Start at the next future quarter-hour only, record the operational gap, and
   backfill nothing. Reapply the first-four-boundary startup gate before declaring
   recovery healthy.

No scored rollback, database transfer, service stop/start, or sentinel/grant
change is executed by this runbook update.

## Post-migration acceptance checklist

- Exact source/config/schema/runtime identities pass.
- Exactly one supervisor and one intended worker exist.
- Integrity, FK and duplicate checks pass; no backfill occurred.
- Mac writer is absent and no unexpected gap remains open.
- Provider/tool restrictions are unchanged.
- Four consecutive prospective scheduled boundaries complete correctly.
- Snapshot/source/decision lineage hashes validate.
- No prompt, model, Quant, Hybrid, detector, simulator, feature schema or Phase
  3 criterion changed because of the host move.
- Protective/no-live-trading restrictions retain their existing state.

Do not mark migration accepted until every item is recorded in a cutover
report. The templates in `deploy/systemd` are not installed or enabled by this
repository change.
