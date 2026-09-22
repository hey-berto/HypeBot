# Phase 2 epoch005 evidence-window candidate

Status: review-only. No production database, authorization artifact, provider
call, service start, or scored evidence is created by this candidate.

Epoch005 keeps the epoch004 model, prompt, output schema, strategies, detector,
simulator, risk, cost, cadence, information boundary, and no-tool contract. Its
inactive configuration differs only in epoch ID and database path.

The operational evidence clock is now explicit and fail closed. Each worker
process records a `WORKER_START_ATTEMPT` before data readiness. After grant,
manifest, runtime identity, network, and data-readiness checks pass, scheduler
entry atomically persists:

1. the immutable `PROSPECTIVE_START_ESTABLISHED` worker-start anchor; and
2. exactly one evidence window whose first eligible timestamp is the first UTC
   quarter-hour strictly after that anchor.

The window stores the anchor integrity hash, establishment timestamp, and rule
version `FIRST_QUARTER_HOUR_STRICTLY_AFTER_WORKER_START_V1`. A unique epoch key
and immutable update/delete triggers prevent resets. Restarts verify the same
window and retain genuine post-start downtime accounting. If a first worker
fails before establishing the window, a later attempt sees the earlier attempt
record and fails closed instead of silently resetting the epoch.

Evidence views require a valid window. Operational telemetry requires exactly
one integrity-valid window and never falls back to the manifest authorization
timestamp. The four-boundary startup gate observes health only; it cannot
create, replace, or move the window. Missing, rejected, or failed boundaries
after establishment remain audit evidence and do not change the 42-day anchor.

The research candidate must receive independent review before an epoch005
operational pin candidate is installed. Activation requires a separate explicit
authorization and a fresh DB, receipt, grant, and manifest. Epoch004 remains a
sealed failed-activation incident and is never copied, relabelled, or reused.
