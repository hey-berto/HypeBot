# HYPE Autopilot — Phase 2 fixes and Ubuntu non-scored acceptance report

Date: 2026-09-10  
Scope: operational integration and non-scored validation only  
Branch: `codex/phase2-ubuntu-operational-integration`  
Disposition: `PHASE_2_FIXES_AND_UBUNTU_NON_SCORED_ACCEPTANCE_READY_FOR_REVIEW`

## Hard-stop compliance

- Scored `phase2_epoch_002` remained stopped. No supervisor or worker is
  loaded, no process has its database open, and no service was installed,
  enabled, or started on Ubuntu.
- The production database and WAL remained byte-identical across the final
  read-only audit:
  - DB: `64443674cd00242611c3a256c6c531f43e65c537266c8404c579e1ac26f29588`
  - WAL: `3d52882420ffab49aa183291d02892032108552e67d5f0e69b7035e7c3cbde3b`
- No production outcome exclusion, evidence window, downtime row, historical
  simulator progression, backfill, or LLM output was written.
- Phase 1 was not modified. No Mac-to-Ubuntu production cutover occurred and
  no concurrent writer was started.
- No prompt, model, output schema, feature schema, Quant strategy, Hybrid
  policy, Detector logic, simulator economics, or Phase 3 threshold changed.
  Relative to the Phase 2 recovery parent, the relevant research-definition
  paths are byte-identical. Relative to the Phase 3 parent, only
  `phase3/operational.py` changed, to scope evidence-window lookup to the
  manifest's `phase2_epoch_id`; Phase 3 configs and gate criteria are unchanged.

## Exact ancestry and branch reconciliation

The integration branch was created from exact Phase 2 recovery commit
`33d410bd9e9da2401eef357a635349658982b7d7` and merged exact Phase 3 metadata
commit `3b478af758940e53a102fca4a0c43c87d81dddb5`. Neither reviewed commit was
rebased, squashed, or rewritten. Merge commit
`0b07c3905006ea1b58a47f1475ccc90d711f18dd` has those two exact SHAs as its
parents. Both remain ancestors of the review branch.

Implementation commits after the merge:

- `31be6cf` — mandatory recovery audit and fatal-stop controls;
- `9623538` — deterministic WebSocket recovery behavior;
- `81119b5` — transport-only provider retry classification;
- `9fc24b6` — Ubuntu-safe service, grant, health, backup and data-root controls;
- `b63fdde` — Ubuntu target dependency lock and migration documentation;
- `172a09b` — transport retry scheduler acceptance evidence;
- `aaf4a31` — bounded Ubuntu public market-data acceptance probe;
- `221b5e4` — corrected systemd start-limit placement.

The final report commit is intentionally added after this evidence summary;
reviewers must use the remote branch head printed in the publication note.

## Frozen identities

- Phase 2 config file SHA-256:
  `b40eeb771ae15cd007bbc9c39da850f55befef6614595862c78e2184cff08c5b`
- Canonical Phase 2 config hash:
  `cdc27292d590fa0fcde60987decfb9479d76316af0aa28b7b51e053c67335b31`
- Prompt SHA-256:
  `c556b5d5f9ca7b9e4c6b7aaa11b40af137c7f98c22a20a2804db6373872e5f78`
- Output-schema hash:
  `3e40e4617cb118c48dc859c9c7b3eb63d0fc0b5a7df5ccd112bf3168aaa25059`
- Candidate database-schema hash:
  `2ed4cb44c89833a06e3624103cac413723fcf2f1f234e57bac749adca3c9e129`
- Pipeline file SHA-256:
  `afbfc702e925d41c15f0983c8c16a8bae14a32081b5bdebd58c1a923ca0c5c0e`
- Simulator file SHA-256:
  `db8add74ee17b66b6b378047bf05c923fc7b85354ea7ec44e3239cad8204a113`
- Provider/model: `openai` / `gpt-5.6-terra` / `gpt-5.6-terra`
- Production config gates: `evidence_collection_enabled=false`,
  `activation_authorized=false`.

## Claude H1 — explicit downtime accounting

At scheduler startup, elapsed UTC quarter-hours not already represented are
recorded once as immutable `REJECTED / PROCESS_DOWNTIME` cycles. Each audit row
states that market collection, snapshot construction, provider invocation,
decision creation, simulator progression, trades, orders, fills and historical
backfill are all false. Deterministic cycle IDs and the existing uniqueness
constraint make repeated startup idempotent. Tests cover multiple missed
boundaries, repeated accounting, activation edge alignment, exact-boundary
clock behavior, and the complete absence of prohibited artifacts.

## Claude H2 — fatal recovery distinction

Manifest, runtime-identity, startup accounting and `recover_before` failures
now emit structured `FATAL / RECOVERY_FAILED` audit and health records and
raise `FatalPhase2OperationalError`, stopping the scheduler process. Ordinary
collection/boundary failures remain contained `REJECTED` cycles. Targeted tests
prove both paths and prevent fatal recovery defects from being disguised as an
ordinary rejected research boundary.

## Whole-supervisor crash and single-writer recovery

The repository-owned supervisor uses two kernel `flock` leases. Diagnostic
metadata never grants ownership. On a new supervisor instance it validates the
prior worker's PID, process start identity and process group, terminates only a
fully verified orphan group, proves every group member dead, and then requires
the worker flock to be free before launch. Unverified or stale metadata causes
no destructive action. Cross-process tests on macOS and Ubuntu cover:

- second-writer rejection;
- worker crash and restart;
- supervisor SIGKILL leaving an orphan worker;
- new supervisor orphan discovery and exact-group termination;
- proof of no overlapping writer and clean SQLite integrity/FKs.

## Retry and WebSocket behavior

- Only transport/network/timeouts receive one retry. HTTP 408/429/5xx and
  transport failures are distinct from non-retryable request/response contract
  errors. Malformed JSON, Pydantic/schema validation and programming/data-
  contract failures fail closed immediately with distinct reason codes.
- The official-SDK WebSocket path has deterministic injection seams, bounded
  exponential backoff `1,2,4,8,16,32,60,60…`, structured connected,
  disconnected, REST-recovery-complete and recovery-failed events, idempotent
  REST catch-up, and interruptible shutdown.
- No provider identity, model, tool policy, prompt, output schema or decision
  adapter changed. Provider tools remain an explicit empty list.

## Phase 3 multi-epoch isolation

Operational telemetry now selects an evidence window by the manifest's exact
`phase2_epoch_id`, rather than accepting a newer window from another epoch.
The multi-epoch test inserts a globally newer foreign window and proves it is
ignored. Phase 3 analysis thresholds and methodology are unchanged.

## Ubuntu runtime controls

Target: `mmt2`, Ubuntu 24.04.3 LTS, Linux x86_64, Python 3.12.3, SQLite 3.45.1,
systemd 255.

- Third-party runtime dependencies are pinned to the Mac material versions in
  `requirements/ubuntu-x86_64-py312.lock`, SHA-256
  `926ce951ded8332e297d0d50a0e07f30ce3a22b7d6e8a8027be743d0e6a9aef2`.
  A fresh Ubuntu virtual environment installed it with `--require-hashes`, the
  local package was installed with `--no-deps`, and `pip check` passed.
- The runtime accepts an explicit `/var/lib` data root and separate lock path,
  while resolving paths and rejecting sidecars, non-SQLite paths, symlink
  escapes and Phase 1/out-of-root databases.
- Repository-owned worker/supervisor entrypoints replace hard-coded Mac paths
  and do not use `caffeinate`.
- A mode-0600, single-use authorization receipt can be atomically consumed into
  a phrase-free durable grant. Startup only reads the grant and reconciles it
  to an already-persisted immutable manifest. No grant was created in this run.
- Production service, five-minute sanitized health timer, tmpfiles lock rule
  and external `OnFailure` alert hook are review templates only. Temporary
  user/path-substituted copies passed `systemd-analyze verify` cleanly on
  Ubuntu. Nothing was installed or enabled. The production unit retains the
  unresolved integration-commit placeholder until review approval.
- The health payload includes only process state, latest boundary freshness,
  gap count, duplicate counts, integrity and FK status. Tests reject PnL,
  returns and win-rate fields.
- SQLite backup uses the online backup API, refuses overwrite, records SHA-256,
  and validates integrity/FKs. The Ubuntu non-scored backup passed with SHA-256
  `185c6e42085e4a70aa2eaeedae4bdd8ea176fd974e868a795d9a16f4f2a03f15`.

## Cross-host deterministic replay

Mac and Ubuntu replay JSON files compared byte-for-byte equal:

- serialized file SHA-256:
  `f34561b1d5782a4b127b33e3c87c31f4cdd4b1097b1b311f0b950e49852c9580`
- fixture hash:
  `12032c4864c43d575caa9253fdef8905bcc6cbc7dac7d2d2caa39538a533a4bc`
- raw-input hash:
  `fa7f082c80e8aaf94d66d53a46b462dc6a9a4a3e07aaa3aa7b6a84d6161d3f2c`
- snapshot hash:
  `e46094d11450c2d4343cb6fdaeabc51a0d40205eab1ceba60c5c2a74a5d63bdb`
- replay hash:
  `02213957b1c723cd4eed28a033d66e04b68fff6710ce3f225b14811e10cc994c`

No mismatch was normalized or waived.

## Ubuntu non-scored acceptance evidence

All artifacts are isolated under
`/home/berto/hypebot-nonscored-evidence`; none uses the production epoch or DB
name.

- Public market-data probe: `PASS`; WebSocket connected and persisted a HYPE
  market observation; SQLite integrity `ok`; zero FK violations; zero provider
  calls, tool calls, scored rows, wallet/order/live-trading capability. Report
  SHA-256:
  `279f829ef6243cced8378f19142f36c4a29a04f884f9023a7c0192e2ac84fd14`.
- Full actual scheduler/simulator recovery harness:
  `PHASE_2_SIMULATOR_ACCEPTANCE_PASSED`, zero blocking witnesses. Report
  SHA-256:
  `25dd3e4eda3f098f2118be010e2f426cc2f958bb1d4b8c58f7b4be43e5676808`.
- Normal progression proves `PENDING_ENTRY → OPEN → CLOSED`, authoritative
  order/fill histories and same-boundary duplicate skipping.
- Pending-without-eligible-bar, TTL exit, adverse-first stop, transport retry,
  retry exhaustion, malformed fail-closed, interrupted collection, interrupted
  trade/order, interrupted attempt/decision, interrupted entry fill and double
  crash raw-response recovery cases all passed.
- Every harness DB reports integrity `ok`, zero FK violations, zero duplicate
  cycle/snapshot/strategy/detector/LLM-attempt/trade/order/fill groups, no
  pre-activation boundary, and no production scoring capability.
- Raw provider fixture plaintext/hash and parsed-output lineage checks passed;
  tool-call count remained zero. Fixture output is explicitly non-scored and is
  not evidence of real-provider availability.

## Regression results

- macOS integration checkout: all 146 tests passed.
- Ubuntu hash-locked checkout: 145 passed and one macOS-pinned legacy
  supervisor witness was correctly skipped; all portable and Ubuntu-relevant
  tests passed.
- Changed Python files pass import-order and mutable-class-default lint checks;
  `git diff --check` passes.

## Production database read-only audit

Final read-only checks on the stopped scored database:

- integrity: `ok`;
- foreign-key violations: `0`;
- duplicate cycle IDs: `0`;
- duplicate snapshot IDs: `0`;
- duplicate strategy decision IDs: `0`;
- duplicate LLM attempt `(snapshot, attempt)` groups: `0`;
- open collection gaps: `0`;
- latest historical boundary remains `2026-09-04T10:30:00+00:00`.

These values are operational integrity evidence only. No strategy performance
was queried or interpreted.

## Review boundary and remaining prohibited actions

This report makes the implementation ready for independent review, not ready
for production deployment. Reviewers must inspect the final remote SHA, replace
and approve the production template's exact commit pin, review the local alert
adapter, and separately authorize service-account provisioning, production DB
transfer, exclusion/evidence-window writes and scored restart. None of those
actions is included here.

`PHASE_2_FIXES_AND_UBUNTU_NON_SCORED_ACCEPTANCE_READY_FOR_REVIEW`
