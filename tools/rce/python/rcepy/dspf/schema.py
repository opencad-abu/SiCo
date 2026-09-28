"""SQLite schema and connection policy for DSPF indexes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from ._sqlite import sqlite3


SCHEMA_VERSION = "1"
_REQUIRED_TABLES = {
    "metadata", "subcircuit", "layer", "net", "node", "resistor",
    "capacitor", "port", "instance_pin", "device_instance", "diagnostic",
}
_REQUIRED_INDEXES = {
    "idx_net_name", "idx_node_name", "idx_node_net", "idx_resistor_net",
    "idx_resistor_node1", "idx_resistor_node2", "idx_capacitor_net",
    "idx_capacitor_node1", "idx_capacitor_node2", "idx_port_net",
    "idx_instance_pin_net", "idx_diagnostic_severity", "idx_diagnostic_line",
    "idx_diagnostic_net",
}
_REQUIRED_METADATA = {
    "schema_version", "status", "source_fingerprint", "source_path",
    "file_size", "total_lines", "bytes_read", "record_count", "counts",
    "warning_count", "error_count", "elapsed_seconds",
}

_SCHEMA = """
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE subcircuit (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    line_start INTEGER NOT NULL,
    line_end INTEGER
);
CREATE TABLE layer (
    id INTEGER PRIMARY KEY,
    number INTEGER NOT NULL UNIQUE,
    name TEXT NOT NULL,
    itf TEXT
);
CREATE TABLE net (
    id INTEGER PRIMARY KEY,
    subcircuit_id INTEGER NOT NULL REFERENCES subcircuit(id),
    name TEXT NOT NULL,
    declared_cap REAL,
    raw_cap TEXT,
    line_start INTEGER NOT NULL,
    line_end INTEGER,
    UNIQUE(subcircuit_id, name)
);
CREATE TABLE node (
    id INTEGER PRIMARY KEY,
    subcircuit_id INTEGER NOT NULL REFERENCES subcircuit(id),
    name TEXT NOT NULL,
    net_id INTEGER REFERENCES net(id),
    ownership INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'internal',
    x REAL,
    y REAL,
    layer TEXT,
    source_line INTEGER,
    UNIQUE(subcircuit_id, name)
);
CREATE TABLE resistor (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    declared_net_id INTEGER NOT NULL REFERENCES net(id),
    node1_id INTEGER NOT NULL REFERENCES node(id),
    node2_id INTEGER NOT NULL REFERENCES node(id),
    value REAL NOT NULL,
    raw_value TEXT NOT NULL,
    layer TEXT,
    length REAL,
    width REAL,
    source_line INTEGER NOT NULL
);
CREATE TABLE capacitor (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    declared_net_id INTEGER NOT NULL REFERENCES net(id),
    node1_id INTEGER NOT NULL REFERENCES node(id),
    node2_id INTEGER NOT NULL REFERENCES node(id),
    value REAL NOT NULL,
    raw_value TEXT NOT NULL,
    layer TEXT,
    source_line INTEGER NOT NULL
);
CREATE TABLE port (
    id INTEGER PRIMARY KEY,
    net_id INTEGER NOT NULL REFERENCES net(id),
    node_id INTEGER NOT NULL REFERENCES node(id),
    direction TEXT,
    capacitance REAL,
    x REAL,
    y REAL,
    source_line INTEGER NOT NULL
);
CREATE TABLE instance_pin (
    id INTEGER PRIMARY KEY,
    net_id INTEGER NOT NULL REFERENCES net(id),
    node_id INTEGER NOT NULL REFERENCES node(id),
    instance_name TEXT,
    pin_name TEXT,
    direction TEXT,
    capacitance REAL,
    x REAL,
    y REAL,
    source_line INTEGER NOT NULL
);
CREATE TABLE device_instance (
    id INTEGER PRIMARY KEY,
    subcircuit_id INTEGER NOT NULL REFERENCES subcircuit(id),
    name TEXT NOT NULL,
    model TEXT,
    raw_summary TEXT,
    source_line INTEGER NOT NULL
);
CREATE TABLE diagnostic (
    id INTEGER PRIMARY KEY,
    severity TEXT NOT NULL,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    source_line INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    net_id INTEGER REFERENCES net(id),
    raw_summary TEXT
);
"""

_SECONDARY_INDEXES = """
CREATE INDEX idx_net_name ON net(name);
CREATE INDEX idx_node_name ON node(name);
CREATE INDEX idx_node_net ON node(net_id);
CREATE INDEX idx_resistor_net ON resistor(declared_net_id);
CREATE INDEX idx_resistor_node1 ON resistor(node1_id);
CREATE INDEX idx_resistor_node2 ON resistor(node2_id);
CREATE INDEX idx_capacitor_net ON capacitor(declared_net_id);
CREATE INDEX idx_capacitor_node1 ON capacitor(node1_id);
CREATE INDEX idx_capacitor_node2 ON capacitor(node2_id);
CREATE INDEX idx_port_net ON port(net_id);
CREATE INDEX idx_instance_pin_net ON instance_pin(net_id);
CREATE INDEX idx_diagnostic_severity ON diagnostic(severity);
CREATE INDEX idx_diagnostic_line ON diagnostic(source_line);
CREATE INDEX idx_diagnostic_net ON diagnostic(net_id);
"""


def connect_database(
    path: str | Path,
    *,
    read_only: bool = False,
    bulk_load: bool = False,
) -> sqlite3.Connection:
    """Open an index with consistent safety and concurrency settings."""
    target = Path(path).resolve()
    if read_only:
        uri = "file:" + quote(str(target), safe="/") + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
    else:
        connection = sqlite3.connect(str(target))
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA foreign_keys = {'OFF' if bulk_load else 'ON'}")
    connection.execute("PRAGMA busy_timeout = 5000")
    if not read_only:
        connection.execute(f"PRAGMA journal_mode = {'OFF' if bulk_load else 'WAL'}")
        connection.execute(f"PRAGMA synchronous = {'OFF' if bulk_load else 'NORMAL'}")
        connection.execute("PRAGMA temp_store = MEMORY")
    return connection


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(_SCHEMA)
    set_metadata(connection, "schema_version", SCHEMA_VERSION)


def create_secondary_indexes(
    connection: sqlite3.Connection,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> None:
    """Create query indexes after bulk ingest to avoid per-row maintenance."""
    if cancelled is not None:
        connection.set_progress_handler(lambda: int(cancelled()), 10_000)
    try:
        connection.executescript(_SECONDARY_INDEXES)
    finally:
        if cancelled is not None:
            connection.set_progress_handler(None, 0)


def set_metadata(connection: sqlite3.Connection, key: str, value: Any) -> None:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    connection.execute(
        "INSERT INTO metadata(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, payload),
    )


def get_metadata(connection: sqlite3.Connection) -> dict[str, Any]:
    return {row["key"]: json.loads(row["value"]) for row in connection.execute(
        "SELECT key, value FROM metadata"
    )}


def validate_database(
    connection: sqlite3.Connection, *, verify_foreign_keys: bool = False,
) -> None:
    metadata = get_metadata(connection)
    if metadata.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported DSPF index schema")
    if metadata.get("status") != "complete":
        raise ValueError("DSPF index is incomplete")
    missing_metadata = _REQUIRED_METADATA.difference(metadata)
    if missing_metadata:
        raise ValueError(f"DSPF index metadata is missing: {sorted(missing_metadata)}")
    tables = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing_tables = _REQUIRED_TABLES.difference(tables)
    if missing_tables:
        raise ValueError(f"DSPF index tables are missing: {sorted(missing_tables)}")
    indexes = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    missing_indexes = _REQUIRED_INDEXES.difference(indexes)
    if missing_indexes:
        raise ValueError(f"DSPF index indexes are missing: {sorted(missing_indexes)}")
    if verify_foreign_keys:
        failure = connection.execute("PRAGMA foreign_key_check").fetchone()
        if failure is not None:
            raise ValueError("DSPF index has foreign-key failures")
