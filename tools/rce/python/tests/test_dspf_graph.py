from __future__ import annotations

import json
import math
import os
import random
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

import pytest

import rcepy.dspf.analysis as analysis_module
from rcepy.dspf import DspfRepository
from rcepy.dspf.schema import (
    connect_database,
    create_schema,
    create_secondary_indexes,
    set_metadata,
)


Edge = tuple[int, int, float]


def _node_id(index: int) -> int:
    return 101 + 17 * index


def _write_graph_index(
    path: Path, node_count: int, edges: Iterable[Edge],
) -> Path:
    connection = connect_database(path, bulk_load=True)
    edge_count = 0
    try:
        create_schema(connection)
        subcircuit_id = int(connection.execute(
            "INSERT INTO subcircuit(name,line_start,line_end) VALUES ('top',1,1)"
        ).lastrowid)
        net_id = int(connection.execute(
            "INSERT INTO net(subcircuit_id,name,declared_cap,raw_cap,line_start,line_end) "
            "VALUES (?,?,?,?,?,?)",
            (subcircuit_id, "N", 0.0, "0", 1, 1),
        ).lastrowid)
        connection.executemany(
            "INSERT INTO node(id,subcircuit_id,name,net_id,ownership,kind,source_line) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                (_node_id(index), subcircuit_id, f"n{index}", net_id, 2,
                 "subnode", index + 1)
                for index in range(node_count)
            ),
        )

        def resistor_rows():
            nonlocal edge_count
            for index, (left, right, value) in enumerate(edges):
                edge_count = index + 1
                yield (
                    10_001 + 19 * index, f"r{index}", net_id,
                    _node_id(left), _node_id(right), value, str(value), index + 1,
                )

        connection.executemany(
            "INSERT INTO resistor(id,name,declared_net_id,node1_id,node2_id,value,"
            "raw_value,source_line) VALUES (?,?,?,?,?,?,?,?)",
            resistor_rows(),
        )
        counts = {
            "subcircuit": 1, "net": 1, "node": node_count,
            "resistor": edge_count, "capacitor": 0, "port": 0,
            "instance_pin": 0, "device_instance": 0, "diagnostic": 0,
        }
        metadata = {
            "status": "complete",
            "source_fingerprint": {"size": 0},
            "source_path": "synthetic",
            "file_size": 0,
            "total_lines": 0,
            "bytes_read": 0,
            "record_count": edge_count,
            "counts": counts,
            "warning_count": 0,
            "error_count": 0,
            "elapsed_seconds": 0.0,
        }
        for key, value in metadata.items():
            set_metadata(connection, key, value)
        create_secondary_indexes(connection)
        connection.commit()
    finally:
        connection.close()
    return path


def test_compact_graph_handles_parallel_loop_zero_and_isolated_nodes(
    tmp_path: Path,
) -> None:
    edges = [(0, 1, 9.0), (0, 1, 2.0), (1, 2, 0.0), (3, 3, 1.0)]
    index = _write_graph_index(tmp_path / "edge-cases.sqlite3", 5, edges)

    with DspfRepository(index) as repository:
        path = repository.resistance_path("N", "n0", "n2")
        disconnected = repository.resistance_path("N", "n3", "n0")
        isolated = repository.resistance_path("N", "n0", "n4")
        same = repository.resistance_path("N", "n4", "n4")
        summary = repository.net_summary("N")

    assert path.total_resistance == 2.0
    assert path.nodes == ["n0", "n1", "n2"]
    assert path.resistors == ["r1", "r2"]
    payload = path.to_dict()
    assert payload["node_ids"] is path.node_ids
    assert payload["nodes"] is path.nodes
    assert payload["resistor_ids"] is path.resistor_ids
    assert payload["resistors"] is path.resistors
    assert disconnected.status == "no_path"
    assert disconnected.visited_nodes == 1
    assert isolated.status == "no_path"
    assert isolated.visited_nodes == 3
    assert same.status == "ok"
    assert same.total_resistance == 0.0
    assert summary.connected_components == 3
    assert summary.dangling_node_count == 1


def test_compact_heap_preserves_database_node_id_tie_break(tmp_path: Path) -> None:
    edges = [(0, 2, 1.0), (0, 1, 1.0), (2, 3, 1.0), (1, 3, 1.0)]
    index = _write_graph_index(tmp_path / "tie.sqlite3", 4, edges)

    with DspfRepository(index) as repository:
        path = repository.resistance_path("N", "n0", "n3")

    assert path.total_resistance == 2.0
    assert path.nodes == ["n0", "n1", "n3"]
    assert path.resistors == ["r1", "r3"]
    assert path.visited_nodes == 4


def test_component_summary_skips_graph_above_limit(
    tmp_path: Path, monkeypatch,
) -> None:
    index = _write_graph_index(
        tmp_path / "component-limit.sqlite3", 3,
        [(0, 1, 1.0), (1, 2, 1.0)],
    )
    monkeypatch.setattr(analysis_module, "_MAX_COMPONENT_RESISTORS", 1)

    with DspfRepository(index) as repository:
        summary = repository.net_summary("N")

    assert summary.connected_components is None
    assert summary.connected_components_status == "limit_exceeded"
    assert summary.connected_components_message == (
        "Not computed: net exceeds 1 graph resistors"
    )


@pytest.mark.parametrize("seed", range(6))
def test_compact_graph_matches_seeded_reference(tmp_path: Path, seed: int) -> None:
    rng = random.Random(seed)
    node_count = 8 + seed
    edges = [
        (rng.randrange(node_count), rng.randrange(node_count), float(rng.randrange(6)))
        for _ in range(node_count * 3)
    ]
    index = _write_graph_index(tmp_path / f"random-{seed}.sqlite3", node_count, edges)
    expected = _all_pairs_distances(node_count, edges)
    pairs = [
        (rng.randrange(node_count), rng.randrange(node_count))
        for _ in range(10)
    ]

    with DspfRepository(index) as repository:
        summary = repository.net_summary("N")
        paths = [
            repository.resistance_path("N", f"n{left}", f"n{right}")
            for left, right in pairs
        ]

    assert summary.connected_components == _reference_components(node_count, edges)
    edge_by_name = {
        f"r{index}": (left, right, value)
        for index, (left, right, value) in enumerate(edges)
    }
    for (left, right), path in zip(pairs, paths):
        if math.isinf(expected[left][right]):
            assert path.status == "no_path"
            continue
        assert path.status == "ok"
        assert path.nodes[0] == f"n{left}"
        assert path.nodes[-1] == f"n{right}"
        assert path.total_resistance == expected[left][right]
        total = 0.0
        for node1, node2, resistor in zip(
            path.nodes, path.nodes[1:], path.resistors,
        ):
            edge_left, edge_right, value = edge_by_name[resistor]
            assert {int(node1[1:]), int(node2[1:])} == {
                edge_left, edge_right,
            }
            total += value
        assert total == path.total_resistance


def test_compact_graph_100k_peak_rss(tmp_path: Path) -> None:
    edge_count = 100_000
    index = _write_graph_index(
        tmp_path / "chain.sqlite3",
        edge_count + 1,
        ((index, index + 1, 1.0) for index in range(edge_count)),
    )
    python_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join((
        str(python_root), str(python_root.parents[1] / "common/python"),
    ))

    for mode, maximum_mib in (("path", 80.0), ("summary", 70.0)):
        command = (
            "import json,resource,time; "
            "from rcepy.dspf import DspfRepository; "
            f"r=DspfRepository({str(index)!r}); t=time.monotonic(); "
            + (
                "x=r.resistance_path('N','n0','n100000'); p=x.to_dict(); "
                "answer=(x.status,x.total_resistance,len(p['resistor_ids']))"
                if mode == "path"
                else "x=r.net_summary('N'); answer=(x.connected_components,)"
            )
            + "; elapsed=time.monotonic()-t; r.close(); "
            "print(json.dumps({'answer':answer,'elapsed':elapsed,"
            "'rss':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))"
        )
        # A fresh launcher keeps the database-building parent's RSS high-water
        # mark out of the worker's resource measurement.
        launcher = (
            "import subprocess,sys; "
            "p=subprocess.run([sys.executable,'-c',sys.argv[1]],"
            "capture_output=True,text=True,timeout=25); "
            "sys.stdout.write(p.stdout); sys.stderr.write(p.stderr); "
            "raise SystemExit(p.returncode)"
        )
        completed = subprocess.run(
            [sys.executable, "-c", launcher, command], check=True,
            capture_output=True, text=True, env=environment, timeout=30,
        )
        result = json.loads(completed.stdout)
        divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
        rss_mib = result["rss"] / divisor
        assert rss_mib < maximum_mib, (mode, result)
        if mode == "path":
            assert result["answer"] == ["ok", 100_000.0, 100_000]
        else:
            assert result["answer"] == [1]


def _all_pairs_distances(node_count: int, edges: list[Edge]) -> list[list[float]]:
    distances = [[math.inf] * node_count for _ in range(node_count)]
    for node in range(node_count):
        distances[node][node] = 0.0
    for left, right, value in edges:
        distances[left][right] = min(distances[left][right], value)
        distances[right][left] = min(distances[right][left], value)
    for middle in range(node_count):
        for left in range(node_count):
            for right in range(node_count):
                distances[left][right] = min(
                    distances[left][right],
                    distances[left][middle] + distances[middle][right],
                )
    return distances


def _reference_components(node_count: int, edges: list[Edge]) -> int:
    groups = [{node} for node in range(node_count)]
    for left, right, _value in edges:
        left_group = next(group for group in groups if left in group)
        right_group = next(group for group in groups if right in group)
        if left_group is not right_group:
            left_group.update(right_group)
            groups.remove(right_group)
    return len(groups)
