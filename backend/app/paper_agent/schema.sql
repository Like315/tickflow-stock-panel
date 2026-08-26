CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS policy_versions (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    parent_id TEXT REFERENCES policy_versions(id) ON DELETE RESTRICT,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(version)
);
CREATE TABLE IF NOT EXISTS promotion_events (
    id TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL REFERENCES policy_versions(id) ON DELETE RESTRICT,
    previous_policy_id TEXT REFERENCES policy_versions(id) ON DELETE RESTRICT,
    decision TEXT NOT NULL CHECK(decision IN ('promote', 'rollback')),
    reason TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trading_sessions (
    id TEXT PRIMARY KEY,
    trade_date TEXT NOT NULL,
    policy_id TEXT NOT NULL REFERENCES policy_versions(id) ON DELETE RESTRICT,
    mode TEXT NOT NULL CHECK(mode IN ('paper', 'replay', 'shadow')),
    status TEXT NOT NULL,
    candidate_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    summary_json TEXT NOT NULL,
    UNIQUE(trade_date, policy_id, mode)
);
CREATE TABLE IF NOT EXISTS decision_snapshots (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES trading_sessions(id) ON DELETE RESTRICT,
    symbol TEXT NOT NULL,
    decision_time TEXT NOT NULL,
    action TEXT NOT NULL,
    feature_json TEXT NOT NULL,
    feature_hash TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS execution_events (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES trading_sessions(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    order_id TEXT,
    symbol TEXT,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_execution_session_time
    ON execution_events(session_id, occurred_at);
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES trading_sessions(id) ON DELETE RESTRICT,
    as_of TEXT NOT NULL,
    cash REAL NOT NULL,
    equity REAL NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolio_sync_events (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('replace')),
    cash REAL NOT NULL,
    equity REAL NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_reflections (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE REFERENCES trading_sessions(id) ON DELETE RESTRICT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evolution_experiments (
    id TEXT PRIMARY KEY,
    champion_policy_id TEXT NOT NULL REFERENCES policy_versions(id) ON DELETE RESTRICT,
    candidate_policy_id TEXT NOT NULL REFERENCES policy_versions(id) ON DELETE RESTRICT,
    mutation_field TEXT NOT NULL,
    status TEXT NOT NULL,
    champion_metrics_json TEXT NOT NULL,
    candidate_metrics_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS dataset_runs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS runtime_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_versions (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_promotion_events (
    id TEXT PRIMARY KEY,
    model_id TEXT NOT NULL REFERENCES model_versions(id) ON DELETE RESTRICT,
    previous_model_id TEXT REFERENCES model_versions(id) ON DELETE RESTRICT,
    decision TEXT NOT NULL CHECK(decision IN ('promote', 'rollback')),
    reason TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_guard_events (
    id TEXT PRIMARY KEY,
    model_id TEXT NOT NULL REFERENCES model_versions(id) ON DELETE RESTRICT,
    action TEXT NOT NULL CHECK(action IN ('disable', 'rollback')),
    reason TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_orchestration_snapshots (
    session_id TEXT PRIMARY KEY REFERENCES trading_sessions(id) ON DELETE RESTRICT,
    trade_date TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS expert_strategy_versions (
    id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL UNIQUE,
    parent_strategy_id TEXT,
    regime TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('shadow', 'promoted', 'rejected')),
    metrics_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    evaluated_at TEXT
);
CREATE TABLE IF NOT EXISTS expert_strategy_parameter_versions (
    id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    parent_id TEXT REFERENCES expert_strategy_parameter_versions(id) ON DELETE RESTRICT,
    params_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('candidate', 'promoted', 'rejected')),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(strategy_id, version)
);
CREATE TABLE IF NOT EXISTS expert_strategy_parameter_events (
    id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    parameter_version_id TEXT
        REFERENCES expert_strategy_parameter_versions(id) ON DELETE RESTRICT,
    previous_parameter_version_id TEXT
        REFERENCES expert_strategy_parameter_versions(id) ON DELETE RESTRICT,
    decision TEXT NOT NULL CHECK(decision IN ('promote', 'rollback')),
    reason TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
