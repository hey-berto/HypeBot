# Epoch006 Day-42 evidence freeze and evaluation runbook

Do not execute before `2026-11-05T00:15:00Z`. This procedure creates a new,
immutable evidence package; it does not modify, stop, or reconfigure epoch006.

## Frozen V2 identity and implementation authorization

The only authorized database-to-`GateEvidence` derivation specification is
`PHASE3_EPOCH006_DERIVATION_SPEC_V2` at approved commit
`fb16b9a5aa05671d098a6baf5e9fc3bfad8d8798`, canonical SHA-256
`d5387568a639af105165a8e00705edef1509145706dce58519d2e56b1ff3e629`.
Its governance overlay is
`config/phase3/epoch006_derivation_spec_v2_freeze_authorization.yaml`. Before
using an adapter, recompute the parsed-YAML canonical hash and verify every
dependency identity in that record. Any mismatch fails closed. The adapter
may implement only this frozen V2; neither its existence nor this runbook
authorizes evaluation before the cutoff and all other gates are satisfied.

## Cutoff semantics

The final included scheduled boundary is exactly `2026-11-05T00:15:00Z`.
Freeze only after its one `SCORED_PROSPECTIVE` `research_cycles` row is terminal:
`COMPLETE` or a contract-supported fail-closed `REJECTED` state. An incomplete
or missing cutoff row blocks the freeze. A rejected row is preserved as valid
evidence and may lead to an `INCONCLUSIVE` formal evaluation; it is not silently
converted into a completed observation. A scored cycle scheduled after cutoff
blocks this exact-cutoff procedure. Rows created after cutoff solely while
finishing the cutoff cycle are retained as its lineage; causal inputs remain
bounded by that cycle's immutable snapshot. Raw rows after cutoff are not used
by the frozen reader's cutoff filters.

## Preconditions and command

First perform read-only service/lease identity checks and record their output as
`runtime_observation`: active/enabled service, zero restarts, expected writer
and supervisor leases, and installed research/operations identities. The freeze
function validates the database manifest, immutable anchor/window, cutoff cycle,
database/FK integrity, structural duplicates, gap count, binding, and evaluator
SHA. It uses SQLite's online `backup()` API from a read-only source connection,
so the live writer does **not** need to stop.

Run from the validated tooling checkout, with an empty package directory and a
copy of the exact installed epoch006 resolved config:

```python
from datetime import datetime
from pathlib import Path
from hype_autopilot.phase3.day42_evidence_freeze import FreezeRequest, freeze_day42_evidence

freeze_day42_evidence(FreezeRequest(
    source_database=Path("/var/lib/hypebot/phase2/phase2_epoch_006.sqlite3"),
    package_directory=Path("/var/lib/hypebot/frozen/epoch006-day42"),
    binding_path=Path("config/phase3/epoch006_operational_binding_v1.yaml"),
    frozen_config_path=Path("/opt/hypebot/research/config/phase2/phase2_epoch_006.yaml"),
    tool_root=Path("/opt/hypebot/operations"),
    runtime_observation={"attach_read_only_identity_check_output_here": True},
    freeze_timestamp=datetime.now().astimezone(),
))
```

Expected package: `epoch006-cutoff.sqlite3`, `epoch006-frozen-config.yaml`, and
`manifest.json`. All are chmod read-only. The manifest is canonical JSON and
contains its own canonical hash, database SHA/size/schema hash, config file and
canonical config hashes, commits, prompt/output-schema hashes, window identity,
cutoff-cycle state, validation results, runtime observation, and hashes of the
formal evaluator, operational reader, and Day-42 shadow tool.

## Evaluation order

1. Verify package SHA, manifest hash, SQLite integrity, and read-only mode.
2. Preserve the formal Phase 3 result from the frozen package only.
3. Run the non-binding veto, fail-closed, co-eligibility, and return-component
   diagnostics against that same frozen DB.
4. Run the Day-42 shadow with the frozen config's simulator fee/slippage values
   and the preserved formal result mapping.
5. Keep every shadow output labeled non-binding and never use it to rewrite the
   formal disposition.

The evaluator file SHA must be
`e606da9c85a6752de2a0bf3b5ba7e53819d8d78fcf46c0b020cdc5a4c52ce78b`.
The current formal evaluator accepts an already-constructed `GateEvidence`; it
has no implemented production database-to-`GateEvidence` reader. The adapter
is authorized for implementation only against the frozen V2 identity above,
and any mismatch must fail closed. Until that separately reviewed adapter is
implemented and installed, formal primary evaluation must STOP. Do not
substitute the operational telemetry reader: it intentionally excludes
outcomes.

## Frozen simulator reporting limitation

`FROZEN_SIMULATOR_LIMITATION`: the pre-existing simulator may count entry
slippage twice internally—once because the slipped entry price is used in the
gross-return path and once because stored entry slippage is included in
`total_slippage`. Do not modify the simulator, reconstruct a counterfactual
return, or deduct/correct simulator costs in the adapter. Day-42 reporting must
state this limitation and must use the persisted `return_pct` exactly as frozen
V2 requires.

## Stop conditions

**Experiment-invalidating / evidence concern:** manifest or identity mismatch,
wrong anchor/window/cutoff, DB/FK failure, or duplicate structural rows.

**Evaluation-blocking tooling issue:** inability to make a consistent backup,
evaluator hash mismatch, absent resolved config, package/hash failure, or the
missing formal database-to-`GateEvidence` adapter above. Preserve evidence and
stop; do not alter the runtime.

**Valid evidence, potentially INCONCLUSIVE:** terminal rejected boundaries,
open collection gaps already represented in evidence, right-censored positions,
or the frozen gate's own calendar/sample/ESS/regime/trigger conditions.
