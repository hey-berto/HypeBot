# Phase 2 epoch006 operational recovery candidate

This candidate preserves sealed epoch005 and prepares a fresh prospective
`phase2_epoch_006`. It changes no model, prompt, output schema, strategy,
simulator, risk, scoring, provider, or market-data semantics.

## Frozen identities

- Research commit: `41e9e9acc261fd69d32c2d811e9ab4dd556c4620`
- Epoch: `phase2_epoch_006`
- Raw config SHA-256: `287e65d7c56b831de6392d0dd8f3572c99a2553e8c1de7041aee8ea514ed6901`
- Canonical config hash: `c69cf18d642aec9bd6bbaa2c85d3a571f45550be7c8a252221708af403a7f24c`
- Prompt hash: `c556b5d5f9ca7b9e4c6b7aaa11b40af137c7f98c22a20a2804db6373872e5f78`
- Output-schema hash: `97318c27b3765780916efe010c3653fa8f8b097bdddd20ef711d40f41a5a1be4`
- Database-schema hash: `62b5f58020cbaf19338fbfcf8e81c6b4a8f66cc67b635d2fe622e8f6d286586a`
- Model/reasoning: `gpt-5.6-terra` / `medium`

## Narrow activation correction

The epoch005 activation orchestrator queried the realtime next-elapse property
for a monotonic `OnActiveSec` / `OnUnitActiveSec` timer. The empty result
triggered its `ERR` trap about two seconds after startup. The timer unit itself
was correct.

The epoch006 orchestrator queries `NextElapseUSecMonotonic`, rejects empty,
zero, and `infinity`, and retains the full worker data-readiness interval:
`READINESS_WINDOW_SECONDS=120`, plus a ten-second observation allowance. It no
longer requires a worker child at two seconds. A first attempt that exits before
the prospective anchor still fails closed, stops both units, and seals the
epoch without reset or reuse.

No production epoch006 DB, manifest, receipt, grant, service, timer, provider
call, scored evidence, or evidence clock is created by this candidate.
