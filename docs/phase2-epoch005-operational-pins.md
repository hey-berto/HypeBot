# Phase 2 epoch005 operational pins

This review candidate prepares, but does not install or activate, the fresh
prospective `phase2_epoch_005` runtime. It preserves the approved timer
bootstrap (`OnActiveSec=5min`, `OnUnitActiveSec=5min`) and all existing
single-writer, authorization-group, Mullvad, per-provider-call, alerting, and
systemd hardening controls.

## Frozen identities

- Research commit: `8104f19e77d6c3d891d84c3fa447e08cd211eeda`
- Epoch: `phase2_epoch_005`
- Config: `config/phase2/phase2_epoch_005.yaml`
- Config hash: `2118bb72f73495a190eb7550408c260de44da84153868da96f94f886b7030b12`
- Prompt hash: `c556b5d5f9ca7b9e4c6b7aaa11b40af137c7f98c22a20a2804db6373872e5f78`
- Output-schema hash: `97318c27b3765780916efe010c3653fa8f8b097bdddd20ef711d40f41a5a1be4`
- Database-schema hash: `62b5f58020cbaf19338fbfcf8e81c6b4a8f66cc67b635d2fe622e8f6d286586a`
- Model/reasoning: `gpt-5.6-terra` / `medium`
- Database: `/var/lib/hypebot/phase2/phase2_epoch_005.sqlite3`
- Grant: `/etc/hypebot/authorized/phase2-epoch-005.grant.json`

## Activation and evidence-clock procedure

1. Verify the exact research and operations commits, clean detached research
   checkout, installed artifact hashes, disabled/inactive units, no effective
   writer, NTP/disk health, and the approved Singapore Mullvad route.
2. Verify every epoch002/003/004 sealed artifact and confirm that no epoch005
   DB, grant, receipt, manifest, lease, or scored row exists.
3. In one reviewed authorization transaction, create a fresh epoch005 DB with
   exactly one matching manifest and a single-use receipt; consume it to the
   root-controlled, group-readable grant. Never copy an earlier epoch.
4. Enable/start exactly one supervisor. The worker records an immutable start
   attempt; after runtime readiness, it atomically records the worker-start
   anchor and the first eligible boundary strictly after that anchor.
5. A failure before window establishment is a sealed activation incident. It
   must not be retried by moving the clock. Manifest authorization time is
   never a fallback.
6. Validate four consecutive prospective boundaries. This operational gate
   does not change the already-established 42-day evidence-window timestamp.
7. Publish the Evidence Start Report with the manifest time, worker-start
   anchor, immutable window timestamp/rule/hash, first four boundaries,
   provider/raw-response lineage, integrity checks, and zero-backfill proof.

This candidate creates no production artifact and does not start the evidence
clock.
