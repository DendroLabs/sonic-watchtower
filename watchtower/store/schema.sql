-- Watchtower Event Journal Schema

CREATE TABLE IF NOT EXISTS baselines (
    port        TEXT NOT NULL,
    metric      TEXT NOT NULL,
    hour_of_week INTEGER NOT NULL,  -- 0-167 (24*7)
    p50         REAL NOT NULL DEFAULT 0.0,
    p95         REAL NOT NULL DEFAULT 0.0,
    p99         REAL NOT NULL DEFAULT 0.0,
    sample_count INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (port, metric, hour_of_week)
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    source      TEXT NOT NULL CHECK (source IN ('local', 'peer', 'syslog')),
    category    TEXT NOT NULL,
    severity    TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    port        TEXT,
    raw_data    TEXT,  -- JSON
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_severity ON events(severity);

CREATE TABLE IF NOT EXISTS topology (
    local_port        TEXT NOT NULL,
    neighbor_hostname TEXT NOT NULL,
    neighbor_port     TEXT NOT NULL,
    last_seen         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    first_seen        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (local_port, neighbor_hostname, neighbor_port)
);

CREATE TABLE IF NOT EXISTS findings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id      TEXT NOT NULL UNIQUE,  -- e.g. f-20260319-0042
    timestamp       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    severity        TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    summary         TEXT NOT NULL,
    detail          TEXT,
    related_events  TEXT,  -- JSON array of event IDs
    active          INTEGER NOT NULL DEFAULT 1,
    resolved_at     TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_findings_active ON findings(active);
CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity);

CREATE TABLE IF NOT EXISTS peer_state (
    peer_hostname       TEXT PRIMARY KEY,
    last_heartbeat      TEXT,
    last_event_summary  TEXT,
    peer_severity       TEXT CHECK (peer_severity IN ('ok', 'info', 'warning', 'critical')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
