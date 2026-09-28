from __future__ import annotations

from rcepy.dspf._nodes import NodeStore
from rcepy.dspf._sqlite import sqlite3
from rcepy.dspf.schema import create_schema


def _database() -> tuple[sqlite3.Connection, int, int]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    create_schema(connection)
    subcircuit_id = connection.execute(
        "INSERT INTO subcircuit(name,line_start) VALUES ('top',1)"
    ).lastrowid
    net_id = connection.execute(
        "INSERT INTO net(subcircuit_id,name,line_start) VALUES (?,'A',2)",
        (subcircuit_id,),
    ).lastrowid
    assert subcircuit_id is not None
    assert net_id is not None
    return connection, int(subcircuit_id), int(net_id)


def test_repeated_equal_ownership_does_not_rewrite_source_line() -> None:
    connection, subcircuit_id, net_id = _database()
    store = NodeStore(connection)
    node_id = store.get(
        subcircuit_id,
        "A:1",
        net_id=net_id,
        confidence=2,
        kind="subnode",
        x=1.0,
        y=2.0,
        source_line=10,
    )
    store.flush()
    statements: list[str] = []
    connection.set_trace_callback(statements.append)

    assert store.get_element(
        subcircuit_id, "A:1", net_id, 2, False, 99,
    ) == node_id
    store.flush()

    row = connection.execute(
        "SELECT ownership,kind,x,y,source_line FROM node WHERE id=?", (node_id,)
    ).fetchone()
    assert tuple(row) == (2, "subnode", 1.0, 2.0, 10)
    assert not any(statement.startswith("UPDATE node") for statement in statements)
    connection.close()


def test_explicit_connection_upgrades_inferred_node() -> None:
    connection, subcircuit_id, net_id = _database()
    store = NodeStore(connection)
    node_id = store.get_element(
        subcircuit_id, "A:1", net_id, 1, False, 5,
    )

    assert store.get(
        subcircuit_id,
        "A:1",
        net_id=net_id,
        confidence=2,
        kind="subnode",
        x=3.0,
        y=4.0,
        layer="M2",
        source_line=12,
    ) == node_id
    store.flush()

    row = connection.execute(
        "SELECT net_id,ownership,kind,x,y,layer,source_line FROM node WHERE id=?",
        (node_id,),
    ).fetchone()
    assert tuple(row) == (net_id, 2, "subnode", 3.0, 4.0, "M2", 12)
    connection.close()


def test_store_reuses_node_from_prepopulated_database() -> None:
    connection, subcircuit_id, net_id = _database()
    first_store = NodeStore(connection)
    node_id = first_store.get_element(
        subcircuit_id, "A:1", net_id, 1, False, 3,
    )
    first_store.flush()

    second_store = NodeStore(connection)

    assert second_store.get_element(
        subcircuit_id, "A:1", net_id, 1, False, 4,
    ) == node_id
    second_store.flush()
    assert connection.execute("SELECT count(*) FROM node").fetchone()[0] == 1
    connection.close()


def test_dirty_node_survives_bounded_cache_eviction(monkeypatch) -> None:
    import rcepy.dspf._nodes as nodes_module

    monkeypatch.setattr(nodes_module, "_CACHE_SIZE", 2)
    monkeypatch.setattr(nodes_module, "_SEEN_BYTES", 8)
    connection, subcircuit_id, net_id = _database()
    store = NodeStore(connection)
    first_id = store.get_element(
        subcircuit_id, "A:1", net_id, 1, False, 3,
    )
    store.flush()
    store.get(
        subcircuit_id, "A:1", net_id=net_id, confidence=2,
        kind="subnode", x=7.0, source_line=8,
    )
    store.get_element(subcircuit_id, "A:2", net_id, 1, False, 9)
    store.get_element(subcircuit_id, "A:3", net_id, 1, False, 10)

    assert store.get_element(
        subcircuit_id, "A:1", net_id, 1, False, 11,
    ) == first_id
    store.flush()
    assert connection.execute("SELECT count(*) FROM node").fetchone()[0] == 3
    row = connection.execute(
        "SELECT ownership,kind,x,source_line FROM node WHERE id=?", (first_id,)
    ).fetchone()
    assert tuple(row) == (2, "subnode", 7.0, 8)
    connection.close()
