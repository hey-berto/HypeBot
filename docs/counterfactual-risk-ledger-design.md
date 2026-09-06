# Counterfactual Risk Ledger design — not implemented

This is a design artifact only. No counterfactual ledger, outcome computation,
or active-evidence read path is shipped in this branch.

## View 1: conditional shadow ledger

For a proposal rejected by one or more frozen risk gates, a future authorized
offline job may simulate that exact proposal under one frozen execution policy.
Its output must be labelled `CONDITIONAL_DIAGNOSTIC_ONLY`. It answers what the
proposal would have done conditional on having occurred in the adverse state
that caused rejection. It must never be called “loss prevented,” treated as a
causal gate effect, or used alone to loosen or tighten a risk limit.

## View 2: full-window single-gate ablation

For each gate selected before a run, a future authorized job will rerun the
entire evaluation window with that one gate disabled from the start. Strategy,
signal, execution, cost, funding and every other risk assumption remain frozen.
The run owns a separate account-state path because accepting one rejected trade
can change margin, drawdown, later eligibility and subsequent decisions.

Each ablation must record the baseline manifest hash, disabled gate, simulator
hash, full input-window hash, initial account-state hash, downstream state-path
hash and every receipt produced. Gate interactions are reported explicitly.
The result is a policy-path comparison, not an isolated randomized causal
treatment.

## Review gate before implementation

Implementation requires a separate approval that freezes window selection,
initial state, handling of simultaneous gate failures, downstream path
divergence, cost assumptions and comparison statistics. Conditional shadow P&L
cannot ship by itself.
