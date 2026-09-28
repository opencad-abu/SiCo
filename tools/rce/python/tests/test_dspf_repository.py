from __future__ import annotations

from pathlib import Path

import pytest

from rcepy.dspf import DspfRepository, build_index
from rcepy.dspf._sqlite import sqlite3
import rcepy.dspf.repository as repository_module


FIXTURES = Path(__file__).with_name("dspf_fixtures")


def test_repository_closes_connection_when_validation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InvalidConnection:
        closed = False

        def close(self) -> None:
            self.closed = True

    connection = InvalidConnection()

    def fail_validation(_connection) -> None:
        raise ValueError("invalid index")

    monkeypatch.setattr(repository_module, "connect_database", lambda *_args, **_kwargs: connection)
    monkeypatch.setattr(repository_module, "validate_database", fail_validation)

    with pytest.raises(ValueError, match="invalid index"):
        DspfRepository(tmp_path / "invalid.sqlite3")

    assert connection.closed


@pytest.fixture
def qrc_repository(tmp_path: Path):
    result = build_index(FIXTURES / "qrc_1_0.dspf", cache_dir=tmp_path)
    with DspfRepository(result.index_path) as repository:
        yield repository


def test_coupling_capacitor_is_stored_once_but_visible_from_both_nets(
    qrc_repository: DspfRepository,
) -> None:
    incoming = qrc_repository.list_capacitors("IN")
    outgoing = qrc_repository.list_capacitors("OUT")

    left = next(item for item in incoming if item["name"] == "Cc_io")
    right = next(item for item in outgoing if item["name"] == "Cc_io")
    assert left["id"] == right["id"]
    assert qrc_repository.info()["counts"]["capacitor"] == 3
    assert qrc_repository.count_capacitors("IN") == 2
    assert qrc_repository.count_capacitors("OUT") == 2


def test_capacitor_queries_use_visibility_indexes_without_full_table_scan(
    qrc_repository: DspfRepository,
) -> None:
    statements: list[str] = []
    qrc_repository._connection.set_trace_callback(statements.append)
    try:
        assert qrc_repository.count_capacitors("IN") == 2
        assert len(qrc_repository.list_capacitors("IN", sort_by="source_line")) == 2
    finally:
        qrc_repository._connection.set_trace_callback(None)

    queries = [item for item in statements if "visible_capacitor" in item]
    assert len(queries) == 2
    for query in queries:
        details = [
            str(row["detail"])
            for row in qrc_repository._connection.execute(
                "EXPLAIN QUERY PLAN " + query
            )
        ]
        plan = "\n".join(details)
        assert "idx_capacitor_net" in plan
        assert "idx_capacitor_node1" in plan
        assert "idx_capacitor_node2" in plan
        assert not any(
            detail.startswith("SCAN") and "TABLE capacitor" in detail
            for detail in details
        )


def test_repository_paginates_sorts_and_supports_exact_net_lookup(
    qrc_repository: DspfRepository,
) -> None:
    assert qrc_repository.count_nets(exact_name="IN") == 1
    assert qrc_repository.list_nets(exact_name="IN")[0]["name"] == "IN"
    assert qrc_repository.list_nets(offset=1, limit=1, sort_by="name")[0]["name"] == "OUT"
    with pytest.raises(ValueError, match="mutually exclusive"):
        qrc_repository.list_nets(search="I", exact_name="IN")
    with pytest.raises(ValueError, match="sort column"):
        qrc_repository.list_nets(sort_by="name; DROP TABLE net")
    with pytest.raises(ValueError, match="Pagination"):
        qrc_repository.list_nodes("IN", limit=0)


@pytest.mark.parametrize(
    ("offset", "limit"),
    [
        (True, 1),
        (0, False),
        (1.0, 1),
        (0, 1.0),
        ("0", 1),
        (0, "1"),
        (0, float("nan")),
    ],
)
def test_pagination_rejects_non_integer_values(
    qrc_repository: DspfRepository, offset: object, limit: object,
) -> None:
    with pytest.raises(ValueError, match="Pagination"):
        qrc_repository.list_nets(offset=offset, limit=limit)  # type: ignore[arg-type]


def test_instance_section_model_devices_do_not_enter_parasitic_tables(
    qrc_repository: DspfRepository,
) -> None:
    info = qrc_repository.info()

    assert info["counts"]["device_instance"] == 3
    assert qrc_repository.count_resistors("IN") == 1
    assert qrc_repository.count_capacitors("IN") == 2
    assert {item["code"] for item in qrc_repository.list_diagnostics()} == {
        "model_device_ignored"
    }


def test_cross_net_resistor_is_listed_only_on_its_declared_net(
    tmp_path: Path,
) -> None:
    result = build_index(
        FIXTURES / "calibre_1_5_errors.dspf", cache_dir=tmp_path,
    )
    with DspfRepository(result.index_path) as repository:
        a_resistors = repository.list_resistors("A<0>")
        z_resistors = repository.list_resistors("Z")

        assert repository.count_resistors("A<0>") == 1
        assert repository.net_summary("A<0>").resistor_count == 1
        assert repository.count_resistors("Z") == 2

    assert [item["name"] for item in a_resistors] == ["Rneg"]
    assert {item["name"] for item in z_resistors} == {"Rz", "Rcross"}


def test_later_declared_node_ownership_overrides_coupling_inference(
    tmp_path: Path,
) -> None:
    source = tmp_path / "shuffled.dspf"
    source.write_text(
        "*|DSPF 1.0\n.SUBCKT top A B 0\n*|GROUND_NET 0\n"
        "*|NET A 1P\nC1 B:1 A:1 1P\n*|S (A:1 0 0)\n"
        "*|NET B 1P\n*|S (B:1 1 0)\n.ENDS\n",
        encoding="utf-8",
    )
    result = build_index(source, cache_dir=tmp_path / "cache")
    with DspfRepository(result.index_path) as repository:
        capacitor = repository.list_capacitors("B")[0]
        a_id = repository.get_net("A")["id"]
        b_id = repository.get_net("B")["id"]

    assert capacitor["node1"] == "B:1"
    assert capacitor["node1_net_id"] == b_id
    assert capacitor["node2_net_id"] == a_id


@pytest.mark.parametrize(
    ("fixture", "nets", "resistors", "capacitors"),
    [
        ("qrc_1_0.dspf", 2, 2, 3),
        ("starrc_1_3.spf", 3, 2, 5),
        ("calibre_1_5_errors.dspf", 2, 3, 1),
    ],
)
def test_extractor_fixtures_have_stable_index_counts(
    tmp_path: Path, fixture: str, nets: int, resistors: int, capacitors: int,
) -> None:
    result = build_index(FIXTURES / fixture, cache_dir=tmp_path)

    assert result.counts["net"] == nets
    assert result.counts["resistor"] == resistors
    assert result.counts["capacitor"] == capacitors
    with sqlite3.connect(result.index_path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
