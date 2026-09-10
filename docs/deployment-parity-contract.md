# Deployment Parity Contract — design only

`config/phase3/deployment_parity_v1.yaml` freezes a proposed testnet-only parity
contract before any formal run. `deployment_manifest_template_v1.yaml` lists
every identity that must be populated after a strategy is promoted and before
testnet authorization. The template cannot authorize testnet or mainnet.

The future formal sample requires at least 20 consecutive preregistered
testnet executions, zero unresolved position-reconciliation mismatches,
confirmed exchange-side protective exits, and zero silent partial-fill or
cancel/replace divergences.

Proposed numeric tolerances are frozen before observing testnet results:

- quantity error: at most one lot and 0.10% relative;
- entry-slippage model error: median at most 3 bps, p95 at most 15 bps, and no
  individual execution above 35 bps;
- fee variance: at most 0.5 bps of notional;
- funding variance: at most 0.5 bps of notional per settlement;
- intent-to-confirmed-exchange-state latency: p95 at most 5 seconds.

These numbers are a preregistered proposal, not evidence that the adapter meets
them. Before the formal sample, the implementation commit, adapter/SDK version,
execution policy, risk policy, permission model, instrument metadata and
deployment artifact must be hashed into one immutable deployment manifest.

No Hyperliquid adapter, wallet, agent key, signing path, network call or order
placement is implemented here. The first executable parity work requires a
separate testnet authorization and must preserve trade-only/no-withdrawal
permissions.
