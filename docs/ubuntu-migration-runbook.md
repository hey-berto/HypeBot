# HYPE Autopilot Ubuntu migration runbook

Status: preparation only. These commands are **not authorized for active
cutover**. Do not create an authorization sentinel, enable a service, stop the
Mac writer or transfer the live database until a separate cutover instruction.

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
intentionally invalid until the Ubuntu artifacts pass the non-scored restart,
identity and no-backfill tests and their hashes are frozen.

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

## Rollback

Stop and prove the Ubuntu writer absent before any Mac restart. Preserve the
Ubuntu database as an immutable incident/cutover artifact. Transfer it back
through the same backup/hash/integrity process only if the approved rollback
runbook says the Mac continues the same epoch. Never start both writers and
never overwrite the original pre-cutover backup. A short recorded gap is safer
than overlap or fabricated evidence.

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
