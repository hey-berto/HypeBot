# Phase 2 build boundary

This branch contains the isolated Phase 2 research infrastructure defined by the Notion build
specification. It is build/test only until a separate prospective evidence authorization.

Safety properties:

- `config/phase2/phase2_epoch_001.yaml` keeps `evidence_collection_enabled` and
  `activation_authorized` false.
- Phase 2 database paths must remain inside the Phase 2 worktree and namespace.
- The active Epoch 1 SQLite database and its WAL/SHM files are never opened.
- LLM input is the immutable canonical `DecisionSnapshot` only; tools and external enrichment are
  disabled.
- Scored execution requires an immutable activation manifest created from the exact authorization
  phrase `start Phase 2 evidence collection` and a separately enabled configuration.
- Development tests use synthetic, non-scored fixtures and fake providers. They do not make LLM
  decisions, trade, use a wallet, or place orders.

Build status can be checked without opening any database or making an API call:

```shell
hype-autopilot-phase2 build-status
```
