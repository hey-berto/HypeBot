SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_candles (
  id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, interval TEXT NOT NULL,
  open_time TEXT NOT NULL, close_time TEXT NOT NULL, open REAL NOT NULL,
  high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL,
  trade_count INTEGER, received_at TEXT NOT NULL, observation_class TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  UNIQUE(symbol, interval, open_time, content_hash)
);
CREATE TABLE IF NOT EXISTS raw_market_observations (
  id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, source_timestamp TEXT NOT NULL,
  received_at TEXT NOT NULL, payload_json TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS raw_bbo_observations (
  id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, source_timestamp TEXT NOT NULL,
  received_at TEXT NOT NULL, payload_json TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS raw_funding_observations (
  id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, source_timestamp TEXT NOT NULL,
  received_at TEXT NOT NULL, funding_rate REAL NOT NULL, premium REAL,
  observation_class TEXT NOT NULL, content_hash TEXT NOT NULL,
  UNIQUE(symbol, source_timestamp, content_hash)
);
CREATE TABLE IF NOT EXISTS feature_observations (
  id INTEGER PRIMARY KEY, snapshot_timestamp TEXT NOT NULL, symbol TEXT NOT NULL,
  feature_schema_version TEXT NOT NULL, payload_json TEXT NOT NULL, content_hash TEXT NOT NULL,
  UNIQUE(snapshot_timestamp, symbol, feature_schema_version, content_hash)
);
CREATE TABLE IF NOT EXISTS epochs (
  epoch_id TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT, status TEXT NOT NULL,
  snapshot_schema_version TEXT NOT NULL, feature_schema_version TEXT NOT NULL,
  quant_trend_version TEXT NOT NULL, quant_mean_reversion_version TEXT NOT NULL,
  detector_version TEXT NOT NULL, regime_version TEXT NOT NULL, simulator_version TEXT NOT NULL,
  config_hash TEXT NOT NULL UNIQUE, git_commit_hash TEXT, notes TEXT,
  config_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS epoch_configurations (
  epoch_id TEXT NOT NULL, config_hash TEXT PRIMARY KEY,
  snapshot_schema_version TEXT NOT NULL, feature_schema_version TEXT NOT NULL,
  quant_trend_version TEXT NOT NULL, quant_mean_reversion_version TEXT NOT NULL,
  detector_version TEXT NOT NULL, regime_version TEXT NOT NULL, simulator_version TEXT NOT NULL,
  git_commit_hash TEXT, config_json TEXT NOT NULL, registered_at TEXT NOT NULL,
  UNIQUE(epoch_id, config_hash)
);
CREATE TABLE IF NOT EXISTS decision_snapshots (
  snapshot_hash TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL UNIQUE,
  snapshot_timestamp TEXT NOT NULL, epoch_id TEXT NOT NULL,
  observation_class TEXT NOT NULL, scoreable INTEGER NOT NULL,
  canonical_json TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(epoch_id, snapshot_timestamp, observation_class)
);
CREATE TABLE IF NOT EXISTS snapshot_source_references (
  snapshot_hash TEXT NOT NULL REFERENCES decision_snapshots(snapshot_hash),
  source_name TEXT NOT NULL, source_timestamp TEXT NOT NULL,
  PRIMARY KEY(snapshot_hash, source_name)
);
CREATE TABLE IF NOT EXISTS strategy_decisions (
  decision_id TEXT PRIMARY KEY, snapshot_hash TEXT NOT NULL REFERENCES decision_snapshots(snapshot_hash),
  strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL, decision TEXT NOT NULL,
  payload_json TEXT NOT NULL, model_name TEXT, model_version TEXT, prompt_version TEXT,
  invocation_reasons_json TEXT, input_tokens INTEGER, cached_input_tokens INTEGER,
  output_tokens INTEGER, model_cost_usd REAL, latency_ms INTEGER, schema_valid INTEGER,
  UNIQUE(snapshot_hash, strategy_id, strategy_version)
);
CREATE TABLE IF NOT EXISTS detector_decisions (
  id INTEGER PRIMARY KEY, snapshot_hash TEXT NOT NULL REFERENCES decision_snapshots(snapshot_hash),
  detector_version TEXT NOT NULL, trigger TEXT NOT NULL, payload_json TEXT NOT NULL,
  UNIQUE(snapshot_hash, detector_version)
);
CREATE TABLE IF NOT EXISTS paper_trades (
  paper_trade_id TEXT PRIMARY KEY, strategy_decision_id TEXT NOT NULL REFERENCES strategy_decisions(decision_id),
  strategy_id TEXT NOT NULL, snapshot_hash TEXT NOT NULL REFERENCES decision_snapshots(snapshot_hash),
  direction TEXT NOT NULL, signal_time TEXT NOT NULL, entry_time TEXT, entry_price REAL,
  stop_price REAL, target_price REAL, current_stop_price REAL, highest_price REAL, lowest_price REAL,
  exit_time TEXT, exit_price REAL, exit_reason TEXT, fees REAL NOT NULL DEFAULT 0,
  slippage_cost REAL NOT NULL DEFAULT 0, funding_cost REAL NOT NULL DEFAULT 0,
  gross_pnl REAL NOT NULL DEFAULT 0, net_pnl REAL NOT NULL DEFAULT 0,
  return_pct REAL NOT NULL DEFAULT 0, r_multiple REAL, status TEXT NOT NULL,
  last_processed_at TEXT NOT NULL, flags_json TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_trade_per_strategy
ON paper_trades(strategy_id) WHERE status IN ('PENDING_ENTRY', 'OPEN');
CREATE TABLE IF NOT EXISTS paper_orders (
  order_id TEXT PRIMARY KEY, paper_trade_id TEXT NOT NULL REFERENCES paper_trades(paper_trade_id),
  strategy_decision_id TEXT NOT NULL REFERENCES strategy_decisions(decision_id),
  created_at TEXT NOT NULL, eligible_at TEXT NOT NULL, status TEXT NOT NULL,
  fill_time TEXT, fill_price REAL, flags_json TEXT NOT NULL,
  UNIQUE(strategy_decision_id)
);
CREATE TABLE IF NOT EXISTS paper_fills (
  id INTEGER PRIMARY KEY, paper_trade_id TEXT NOT NULL REFERENCES paper_trades(paper_trade_id),
  fill_time TEXT NOT NULL, fill_type TEXT NOT NULL, price REAL NOT NULL,
  fee REAL NOT NULL, slippage_cost REAL NOT NULL, details_json TEXT NOT NULL,
  UNIQUE(paper_trade_id, fill_type, fill_time)
);
CREATE TABLE IF NOT EXISTS collection_gaps (
  id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, interval TEXT NOT NULL,
  gap_start TEXT NOT NULL, gap_end TEXT NOT NULL, detected_at TEXT NOT NULL,
  recovered_at TEXT, status TEXT NOT NULL,
  UNIQUE(symbol, interval, gap_start, gap_end)
);
CREATE TABLE IF NOT EXISTS research_cycles (
  cycle_id TEXT PRIMARY KEY, scheduled_at TEXT NOT NULL, observation_class TEXT NOT NULL,
  started_at TEXT NOT NULL, completed_at TEXT, status TEXT NOT NULL,
  snapshot_hash TEXT REFERENCES decision_snapshots(snapshot_hash), details_json TEXT NOT NULL,
  UNIQUE(scheduled_at, observation_class)
);
CREATE TABLE IF NOT EXISTS data_quality_events (
  id INTEGER PRIMARY KEY, occurred_at TEXT NOT NULL, severity TEXT NOT NULL,
  code TEXT NOT NULL, details_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS health_events (
  id INTEGER PRIMARY KEY, occurred_at TEXT NOT NULL, component TEXT NOT NULL,
  status TEXT NOT NULL, details_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiment_events (
  event_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, event_type TEXT NOT NULL,
  occurred_at TEXT NOT NULL, git_commit_hash TEXT NOT NULL, config_hash TEXT NOT NULL,
  details_json TEXT NOT NULL,
  UNIQUE(experiment_id, event_type, occurred_at)
);
CREATE TRIGGER IF NOT EXISTS immutable_snapshots_update
BEFORE UPDATE ON decision_snapshots BEGIN SELECT RAISE(ABORT, 'decision snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_snapshots_delete
BEFORE DELETE ON decision_snapshots BEGIN SELECT RAISE(ABORT, 'decision snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_epochs_update
BEFORE UPDATE ON epochs BEGIN SELECT RAISE(ABORT, 'epoch records are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_epochs_delete
BEFORE DELETE ON epochs BEGIN SELECT RAISE(ABORT, 'epoch records are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_epoch_configurations_update
BEFORE UPDATE ON epoch_configurations BEGIN SELECT RAISE(ABORT, 'epoch configurations are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_epoch_configurations_delete
BEFORE DELETE ON epoch_configurations BEGIN SELECT RAISE(ABORT, 'epoch configurations are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_experiment_events_update
BEFORE UPDATE ON experiment_events BEGIN SELECT RAISE(ABORT, 'experiment events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_experiment_events_delete
BEFORE DELETE ON experiment_events BEGIN SELECT RAISE(ABORT, 'experiment events are immutable'); END;
"""
