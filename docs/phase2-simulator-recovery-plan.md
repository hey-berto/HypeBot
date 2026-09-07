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

## Acceptance matrix

1. Run actual Phase 2 boundary orchestration, reconstruction, strategy/runner
   evaluation and simulator against isolated offline fixture data. Preserve the
   frozen V2 provider contract; fixtures are not represented as live responses.
2. Capture pending, entry, order, fill and exit states, including absent eligible
   entry data. Preserve the actual frozen semantics: pending does not expire
   merely because no eligible 1m candle exists; TTL starts after entry.
3. Restart worker processes, repeat boundaries and interrupt persisted work.
   Verify no duplicate groups and exact raw/parsed/hash/lineage invariants.
4. Exercise the existing external supervisor source in an isolated acceptance
   environment with a harmless worker. Record any orphan or overlapping worker.
5. Report crash-consistency or supervision failures as blockers. Passing tests
   that reproduce a defect are not a passing operational acceptance gate.
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
