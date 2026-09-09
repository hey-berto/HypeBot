# Phase 2 simulator-progression acceptance plan

Baseline: `0322e53ba55df1c9ad44286616cd71bd2e0700c6`.
Candidate workspace: `HypeBot-phase2-recovery`, branch
`codex/phase2-simulator-recovery`. This is not the production worktree.

## Authority and isolation

- Epoch 1 recovery is complete and is not repeated by this work.
- `phase2_epoch_002` stays stopped. Its production database and WAL are only
  file-hashed for preservation checks, never opened by the test runtime.
- No historical scored pending trade, boundary, decision, order or fill is
  progressed, repaired, imported or backfilled.
- No real-provider request, wallet, private key, exchange order or migration.
- Tracked research configurations, prompts, schemas, models, strategies, Hybrid,
  detector, features, risk definitions and simulator economics remain unchanged.

## Minimal candidate

The Phase 2 collection/snapshot pipeline omitted the existing simulator's
`process_until(boundary)` operation. Invoke it after market collection/gap
recovery and before building the next snapshot, matching the intended Phase 1
ordering. Do not change the implementation of the frozen simulator.

## Operational recovery design

The reviewed repair keeps the frozen decision contract and simulator economics
unchanged while adding four operational controls:

1. The worker holds a kernel-enforced exclusive writer lease for its whole
   lifetime. A separate supervisor lease prevents concurrent supervisors. Before
   relaunch, the supervisor terminates the exact old process group and proves no
   live member remains. PID start-time evidence guards against PID reuse.
2. Simulator submission and progression run inside SQLite `BEGIN IMMEDIATE`
   transactions. Existing repository commit calls are deferred only inside these
   scopes, so a hard crash rolls back the whole trade/order/fill transition.
3. At every prospective boundary, a provider-free recovery pass finalizes old
   `RUNNING` cycles. A persisted VALID raw response is parsed by the frozen V2
   validator and saved without another provider call. Ambiguous state becomes a
   permanent explicit exception; it is never recollected or backfilled.
4. Append-only outcome exclusions preserve every decision and historical trade
   row while making pre-deployment non-terminal outcomes unavailable to simulator
   progression and evaluation views. The Phase 3 calendar-floor anchor becomes
   the first prospective scored boundary after a reviewed deployment, never the
   original activation timestamp.

The production database is not migrated and the external production supervisor
is not replaced by this candidate until a separate scored-restart authorization.
The future deployment transaction must append the operational deployment,
historical exclusion, and evidence-window records before the worker scores its
first new boundary.

## Acceptance matrix

1. Run actual Phase 2 boundary orchestration, reconstruction, strategy/runner
   evaluation and simulator against isolated offline fixture data. Preserve the
   frozen V2 provider contract; fixtures are not represented as live responses.
2. Capture pending, entry, order, fill and exit states, including absent eligible
   entry data. Preserve the actual frozen semantics: pending does not expire
   merely because no eligible 1m candle exists; TTL starts after entry.
3. Restart worker processes, repeat boundaries and interrupt persisted work.
   Verify no duplicate groups and exact raw/parsed/hash/lineage invariants.
4. Retain the isolated failure witness for the old external supervisor, then run
   the replacement supervisor and writer leases with a harmless worker. Prove
   old-group death, rejection of a concurrent writer, and a clean supervisor
   restart with exactly one worker.
5. Inject crashes after raw response persistence, trade persistence and fill
   persistence, plus a second crash during recovery. Require a deterministic
   terminal state with no provider replay, partial write, duplicate, or stuck
   cycle.
6. Compare the candidate diff and all protected identities against baseline;
   prove scored production DB/WAL byte preservation.
7. Publish one final Notion recovery/acceptance report and stop for review.

## Disposition

The missing invocation can be classified `OPERATIONAL_ONLY` only if the existing
simulator rules are unchanged. Any need to change research semantics requires
`PHASE_2_NEW_EPOCH_REQUIRED`, not an in-place repair.

Acceptance is `PHASE_2_SIMULATOR_ACCEPTANCE_PASSED` only if all required runtime,
restart, idempotency and integrity gates pass; otherwise it is
`PHASE_2_SIMULATOR_ACCEPTANCE_BLOCKED`. Neither result authorizes restarting the
existing scored epoch or retroactively progressing its historical position.
