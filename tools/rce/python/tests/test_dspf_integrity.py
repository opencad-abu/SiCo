from __future__ import annotations

from pathlib import Path

import pytest

from rcepy.dspf import DspfRepository, ResistanceIntegrity, build_index
from rcepy.dspf._sqlite import sqlite3


def _index(tmp_path: Path, name: str, body: str) -> Path:
    source = tmp_path / f"{name}.dspf"
    source.write_text(
        "*|DSPF 1.0\n.SUBCKT top PASS OPEN ONE A B 0\n"
        "*|GROUND_NET 0\n" + body + ".ENDS top\n",
        encoding="ascii",
    )
    return build_index(source, cache_dir=tmp_path / f"{name}-cache").index_path


def test_terminal_open_ignores_synthetic_root_and_reports_islands(
    tmp_path: Path,
) -> None:
    index = _index(tmp_path, "open", """
*|NET PASS 0
*|I (XP:p XP p B 0 0 0)
*|I (XQ:p XQ p B 0 1 0)
*|S (PASS:1 0.5 0)
Rp1 XP:p PASS:1 1
Rp2 PASS:1 XQ:p 2
*|NET OPEN 0
*|I (X1:p X1 p B 0 0 0)
*|I (X2:p X2 p B 0 1 0)
*|I (X3:p X3 p B 0 2 0)
*|S (OPEN:1 1 0)
*|S (OPEN:2 2 0)
Ro X1:p X2:p 1
Ri OPEN:1 OPEN:2 1
*|NET ONE 0
*|I (XO:p XO p B 0 0 0)
""")

    with DspfRepository(index) as repository:
        connected = repository.resistance_integrity("PASS")
        opened = repository.resistance_integrity("OPEN")
        one = repository.resistance_integrity("ONE")

    assert isinstance(connected, ResistanceIntegrity)
    assert connected.open_status == "pass"
    assert connected.terminal_count == 2
    assert connected.terminal_component_count == 1
    assert connected.orphan_node_count == 1  # Unlocated *|NET declaration node.
    assert connected.island_count == 0

    assert opened.open_status == "open"
    assert opened.terminal_count == 3
    assert opened.terminal_component_count == 2
    assert {item.terminal_names for item in opened.open_components} == {
        ("X1:p", "X2:p"), ("X3:p",),
    }
    assert opened.island_count == 1
    assert opened.orphan_node_count == 1
    assert one.open_status == "not_applicable"
    assert one.terminal_count == one.terminal_component_count == 1


def _short_index(tmp_path: Path) -> Path:
    index = _index(tmp_path, "short", """
*|NET A 0
*|P (A I 0 0 0)
*|S (A:1 1 0)
Rlocal A A:1 1
Rbridge A:1 B:1 0.05
Rparallel A:1 B:1 0
Rhigh A:1 B:1 0.2
Rnegative A:1 B:1 -1
Rground A:1 0 0.01
Rloop A:1 A:1 0
Rfloat A:1 FLOAT 0.001
*|NET B 0
*|P (B O 3 0 0)
*|S (B:1 2 0)
Rb B B:1 1
""")
    with sqlite3.connect(index) as db:
        db.execute("UPDATE node SET net_id=NULL WHERE name='FLOAT'")
    return index


def test_short_candidates_are_thresholded_visible_from_both_nets(
    tmp_path: Path,
) -> None:
    index = _short_index(tmp_path)
    with DspfRepository(index) as repository:
        a = repository.resistance_integrity("A", short_threshold_ohm=0.05)
        b = repository.resistance_integrity("B", short_threshold_ohm=0.05)
        exact_zero = repository.resistance_integrity("A", short_threshold_ohm=0)

    assert a.short_status == "short"
    assert a.cross_net_resistor_count == 5
    assert a.short_candidate_count == 3
    assert {item.name for item in a.short_candidates} == {
        "Rbridge", "Rparallel", "Rground",
    }
    assert {item.reason for item in a.short_candidates} == {
        "cross_net_bridge", "ground_bridge",
    }
    assert next(item for item in a.short_candidates if item.name == "Rground").node2_net_name == "ground"
    assert a.inconclusive_boundary_count == 1
    assert a.negative_resistor_count == 1
    assert a.zero_resistor_count == 2
    assert a.self_loop_count == 1

    assert b.short_candidate_count == 2
    assert {item.name for item in b.short_candidates} == {"Rbridge", "Rparallel"}
    assert exact_zero.short_candidate_count == 1
    assert exact_zero.short_candidates[0].name == "Rparallel"


def test_negative_self_loop_and_orphan_endpoint_are_never_shorts(
    tmp_path: Path,
) -> None:
    index = _short_index(tmp_path)
    with DspfRepository(index) as repository:
        result = repository.resistance_integrity("A", short_threshold_ohm=10)

    names = {item.name for item in result.short_candidates}
    assert "Rnegative" not in names
    assert "Rloop" not in names
    assert "Rfloat" not in names
    assert result.short_status == "short"
    assert result.inconclusive_boundary_count == 1


def test_integrity_limits_validation_and_bounded_details(tmp_path: Path) -> None:
    index = _short_index(tmp_path)
    with DspfRepository(index) as repository:
        limited = repository.resistance_integrity("A", max_resistors=1)
        counts_only = repository.resistance_integrity(
            "A", short_threshold_ohm=1, detail_limit=0,
        )
        for value in (-1, float("nan"), float("inf"), True, "1", None):
            with pytest.raises(ValueError, match="finite non-negative"):
                repository.resistance_integrity(
                    "A", short_threshold_ohm=value,  # type: ignore[arg-type]
                )
        for value in (0, -1, True, 1.0, "1", None):
            with pytest.raises(ValueError, match="positive integer"):
                repository.resistance_integrity(
                    "A", max_resistors=value,  # type: ignore[arg-type]
                )
        for value in (-1, True, 1.0, "1", None):
            with pytest.raises(ValueError, match="non-negative integer"):
                repository.resistance_integrity(
                    "A", detail_limit=value,  # type: ignore[arg-type]
                )

    assert limited.status == "limit_exceeded"
    assert limited.open_status == limited.short_status == "limit_exceeded"
    assert counts_only.short_candidate_count > 0
    assert counts_only.short_candidates == ()
    assert counts_only.details_truncated is True
    assert counts_only.to_dict()["name"] == "A"


def test_incident_query_uses_both_resistor_endpoint_indexes(tmp_path: Path) -> None:
    index = _short_index(tmp_path)
    with DspfRepository(index) as repository:
        statements: list[str] = []
        repository._connection.set_trace_callback(statements.append)
        try:
            repository.resistance_integrity("B", short_threshold_ohm=0.1)
        finally:
            repository._connection.set_trace_callback(None)
        query = next(statement for statement in statements if "incident_resistor" in statement)
        plan = "\n".join(
            str(row["detail"])
            for row in repository._connection.execute("EXPLAIN QUERY PLAN " + query)
        )

    assert "idx_resistor_node1" in plan
    assert "idx_resistor_node2" in plan
