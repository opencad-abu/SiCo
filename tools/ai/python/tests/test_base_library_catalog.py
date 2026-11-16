"""Protect source boundaries, CDF identity, and complete read-only reference imports."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2] / "reference/virtuoso-base-libraries"
sys.path.insert(0, str(ROOT))
try:
    import catalog_data
    import doc_extract
    spec = importlib.util.spec_from_file_location("base_library_builder", ROOT / "build_catalog.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    spec = importlib.util.spec_from_file_location("base_library_query", ROOT / "query.py")
    lookup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lookup)
finally:
    sys.path.remove(str(ROOT))


def sample_section():
    return {"library": "analogLib", "cells": ["vdc"], "status": "documented",
            "category": "Independent", "source": "file:///doc/source.html#a",
            "description": "DC voltage source", "title": "vdc Symbol", "parameters": [
                {"name": "vdc", "label": "DC voltage", "simulator_support": {"spectre": "x"}}]}


def runtime_rows():
    return [
        {"kind": "header", "schema_version": 1, "cdf_scope": "base_cell", "symbol_scope": "symbol_only"},
        {"kind": "library", "name": "analogLib", "path": "/installed/analogLib"},
        {"kind": "cell", "library": "analogLib", "cell": "vdc", "views": ["symbol", "spectre"],
         "base_cdf": [{"name": "vdc", "paramType": "string", "defValue": "", "units": "voltage"}],
         "symbol": {"status": "read", "terminals": [{"name": "PLUS", "direction": "inputOutput", "pin_figures": []}]}},
        {"kind": "footer", "complete": True, "cell_count": 1, "error_count": 0},
    ]


@pytest.mark.parametrize("fault", ["truncated", "count", "error_count", "type", "duplicate", "scope", "views"])
def test_reject_invalid_runtime(tmp_path, fault):
    rows = runtime_rows()
    if fault == "truncated":
        rows.pop()
    elif fault == "count":
        rows[-1]["cell_count"] = 3
    elif fault == "error_count":
        rows[-1]["error_count"] = 1
    elif fault == "type":
        rows[2]["base_cdf"][0]["paramType"] = "baseCellData"
    elif fault == "duplicate":
        rows.insert(3, dict(rows[2]))
        rows[-1]["cell_count"] = 2
    elif fault == "scope":
        rows[0]["cdf_scope"] = "effective"
    elif fault == "views":
        rows[2]["views"] = []
    path = tmp_path / "runtime.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    with pytest.raises(ValueError):
        catalog_data.load_runtime(path)


def test_section_stops_before_next_component_and_keeps_support():
    soup = doc_extract.BeautifulSoup('''<h3><a id="a"></a>ppulse Symbol</h3>
        <p>Description</p><p>Pulse source.</p><h4>Component Parameters</h4>
        <table><tr><th>CDF Parameter Label</th><th>CDF Parameter</th><th>spectre</th></tr>
        <tr><td><a href="appA.html#v1">Voltage 1</a></td><td>v1</td><td>x</td></tr></table>
        <h3><a id="b"></a>pprbs Symbol</h3><p>Must not leak.</p>''', "html.parser")
    section = doc_extract.section_at(soup, "a")
    assert "Must not leak" not in str(section)
    tables, params = doc_extract.extract_tables(section, "file:///doc/source.html#a")
    assert params == [{"name": "v1", "label": "Voltage 1", "simulator_support": {"spectre": "x"},
                       "references": ["file:///doc/appA.html#v1"]}]
    assert doc_extract.describe(section) == "Pulse source."
    assert len(tables) == 1


def test_toc_grouped_names_and_unlisted_obsolete_category(tmp_path):
    analog = tmp_path / "analoglibref"
    analog.mkdir()
    nodes = [{"id": "node0", "text": "root", "parent": "#"},
             {"id": "group", "text": "Sources", "parent": "node0"},
             {"id": "a", "text": "pvccs, pvccs2 Symbol", "parent": "group", "href": "shared.html#a"},
             {"id": "b", "text": "vdc Symbol", "parent": "group", "href": "shared.html#b"}]
    (analog / "analoglibref.json").write_text(json.dumps({"core": {"data": nodes}}))
    (analog / "shared.html").write_text('<h3><a id="a"></a>pvccs, pvccs2 Symbol</h3><p>Shared.</p>'
                                       '<h3><a id="b"></a>vdc Symbol</h3><p>DC.</p>')
    basic = tmp_path / "basicLib"
    basic.mkdir()
    (basic / "basiclib.json").write_text(json.dumps({"core": {"data": []}}))
    (basic / "basicLib.html").write_text('<h2><a id="old"></a>Obsolete</h2><p>Old.</p>'
                                       '<table><tr><td>VDD</td><td></td></tr></table>'
                                       '<h2><a id="vh"></a>VHDLPins</h2><table><tr><td>vhdlIPin</td></tr></table>')
    sections, sources = doc_extract.load_documents(tmp_path)
    assert sections[0]["cells"] == ["pvccs", "pvccs2"]
    assert sections[0]["description"] == "Shared."
    assert sections[2]["cells"] == ["VDD"] and sections[2]["status"] == "obsolete"
    assert sections[3]["cells"] == ["vhdlIPin"] and sections[3]["status"] == "listed_only"
    assert len(sources) == 4


def test_references_preserve_case_missing_cells_and_multiple_sections():
    first = sample_section()
    second = dict(first, category="Appendix", source="file:///doc/app.html#b")
    absent = dict(first, cells=["VDC"])
    runtime = {("analogLib", "vdc"): runtime_rows()[2]}
    cells = catalog_data.merge_cells([first, second, absent], runtime)
    by_name = {cell["cell"]: cell for cell in cells}
    assert by_name["vdc"]["section_ids"] == [1, 2]
    assert by_name["VDC"]["runtime_status"] == "not_captured"
    assert by_name["vdc"]["common_parameters"] == ["vdc"]


def test_catalog_queries_are_read_only_literal_and_keep_cdf_namespace(tmp_path):
    sections = [sample_section()]
    cells = catalog_data.merge_cells(sections, {("analogLib", "vdc"): runtime_rows()[2]})
    output = tmp_path / "catalog"
    report = builder.write_catalog(output, cells, sections, {"schema_version": 1})
    assert report["complete"] is True
    database = output / "catalog.sqlite3"
    before = database.read_bytes()
    result = lookup.query(database, "analogLib", "vdc", details=True)
    assert result["cells"][0]["runtime"]["base_cdf"][0]["name"] == "vdc"
    assert result["cells"][0]["runtime"]["symbol"]["terminals"][0]["name"] == "PLUS"
    assert result["cells"][0]["sections"][0]["parameters"][0]["simulator_support"]["spectre"] == "x"
    assert lookup.query(database, search="直流")["total"] == 1
    assert lookup.query(database, search="%")["total"] == 0
    assert lookup.query(database, cell="VDC")["total"] == 0
    assert database.read_bytes() == before
    with pytest.raises(FileExistsError):
        builder.write_catalog(output, cells, sections, {"schema_version": 1})


def test_no_symbol_is_not_missing_cell(tmp_path):
    rows = runtime_rows()
    rows[2].update(cell="alias", views=["spectre"], symbol={"status": "no_symbol_view"})
    path = tmp_path / "runtime.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    runtime, _ = catalog_data.load_runtime(path)
    record = catalog_data.merge_cells([], runtime)[0]
    assert record["runtime_status"] == "captured"
    assert record["doc_status"] == "runtime_only"
    assert record["runtime"]["views"] == ["spectre"]
