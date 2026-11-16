from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from aivworkbench.m1ai import _DbAccessArtifactError, _load_oa_evidence
from aivworkbench.m1ai_inputs import (
    load_contract,
    parse_history_rdb,
    parse_maestro,
    qualify_inputs,
)
from aivworkbench.profiles import product_root


MAESTRO = """<?xml version="1.0"?>
<setupdb version="7">maestro
  <active>Active Setup
    <corners>
      <corner enabled="1">_default</corner>
      <corner enabled="1">C1
        <vars>
          <var>temperature<value>0 27 80</value></var>
          <var>VDD<value>1.15 1.2 1.25</value></var>
        </vars>
        <models><model enabled="1">gpdk045.scs
          <modelsection>"tt" "ff" "ss" "sf" "fs"</modelsection>
        </model></models>
      </corner>
    </corners>
    <tests>
      <test enabled="1">saradc:comparator_noise_TB_new:1
        <tooloptions>
          <option>cell<value>comparator_noise_TB_new</value></option>
          <option>lib<value>saradc</value></option>
        </tooloptions>
        <outputs>
          <output>TotalNoiseatOutput<evalType>point</evalType></output>
          <output>InputReferredNoise<evalType>point</evalType></output>
          <output>noiseDensity<evalType>point</evalType></output>
        </outputs>
      </test>
      <test enabled="1">saradc:comparator_offset_TB_new:1
        <tooloptions>
          <option>cell<value>comparator_offset_TB_new</value></option>
          <option>lib<value>saradc</value></option>
        </tooloptions>
      </test>
    </tests>
    <specs>
      <spec>saradc:comparator_noise_TB_new:1.InputReferredNoise
        <testname>saradc:comparator_noise_TB_new:1</testname>
        <resname>InputReferredNoise</resname><specType>lt</specType>
        <min></min><max>700u</max><target></target><tol></tol>
      </spec>
      <spec>saradc:comparator_offset_TB_new:1.Offset
        <testname>saradc:comparator_offset_TB_new:1</testname>
        <resname>Offset</resname><specType>range</specType>
        <min>-5m</min><max>5m</max><target></target><tol></tol>
      </spec>
    </specs>
  </active>
</setupdb>
"""


PORTS = [
    {"name": "Din+", "direction": "input"},
    {"name": "Din-", "direction": "input"},
    {"name": "clk", "direction": "input"},
    {"name": "outb", "direction": "output"},
    {"name": "out", "direction": "output"},
]


def _cell(library: str, cell: str, view: str) -> dict[str, object]:
    symbol = view == "symbol"
    return {
        "library": library,
        "cell": cell,
        "view": view,
        "opened": True,
        "terminals": PORTS if symbol else PORTS,
        "port_order": ["out", "outb", "Din+", "Din-", "clk"] if symbol else [],
        "instances": (
            []
            if symbol
            else [
                {
                    "name": "I0",
                    "library": "saradcII",
                    "cell": "latch_buffer",
                    "view": "symbol",
                    "connections": [
                        {"terminal": "Din+", "net": "Din-"},
                        {"terminal": "Din-", "net": "Din+"},
                    ],
                }
            ]
        ),
    }


def _catalog() -> dict[str, object]:
    values = [
        _cell("saradc", "comparator", "schematic"),
        _cell("saradc", "comparator", "symbol"),
        _cell("saradcII", "comparator", "schematic"),
        _cell("saradcII", "comparator", "symbol"),
        _cell("saradc", "comparator_new", "schematic"),
        _cell("saradc", "comparator_new", "symbol"),
    ]
    values.extend(
        [
            {
                "library": "saradc",
                "cell": "comparator_noise_TB_new",
                "view": "schematic",
                "opened": True,
                "terminals": [],
                "port_order": [],
                "instances": [
                    {
                        "name": "I9",
                        "library": "saradcII",
                        "cell": "comparator",
                        "view": "symbol",
                        "connections": [],
                    }
                ],
            },
            {
                "library": "saradc",
                "cell": "comparator_offset_TB_new",
                "view": "schematic",
                "opened": True,
                "terminals": [],
                "port_order": [],
                "instances": [
                    {
                        "name": "I0",
                        "library": "saradc",
                        "cell": "comparator",
                        "view": "symbol",
                        "connections": [],
                    }
                ],
            },
        ]
    )
    return {"provider": "dbAccess", "read_only": True, "cellviews": values}


def _history() -> dict[str, object]:
    return {
        "tests": {
            "saradc:comparator_noise_TB_new:1": {
                "results": ["TotalNoiseatOutput", "InputReferredNoise", "noiseDensity"]
            },
            "saradc:comparator_offset_TB_new:1": {
                "results": ["Offset", "Input", "Latch Out"]
            },
        }
    }


def test_maestro_and_oa_evidence_qualify_for_isolated_rebinding(tmp_path: Path) -> None:
    maestro_path = tmp_path / "maestro.sdb"
    maestro_path.write_text(MAESTRO, encoding="utf-8")
    maestro = parse_maestro(maestro_path)
    contract = load_contract(product_root() / "contracts" / "comparator_new.json")
    canonical, evidence = qualify_inputs(
        contract,
        maestro,
        _catalog(),
        _history(),
        manual_sha256=contract["manual_evidence"]["sha256"],
    )

    assert evidence["all_checks_pass"] is True
    assert evidence["ready_for_isolated_rebinding"] is True
    assert canonical["corners"]["matrix_size"] == 45
    assert canonical["rebind_target"] == "saradc/comparator_new/schematic"


def test_wrong_historical_dut_binding_fails_closed(tmp_path: Path) -> None:
    maestro_path = tmp_path / "maestro.sdb"
    maestro_path.write_text(MAESTRO, encoding="utf-8")
    contract = load_contract(product_root() / "contracts" / "comparator_new.json")
    catalog = deepcopy(_catalog())
    testbench = next(
        item
        for item in catalog["cellviews"]
        if item["cell"] == "comparator_offset_TB_new"
    )
    testbench["instances"][0]["cell"] = "some_other_comparator"

    _, evidence = qualify_inputs(
        contract,
        parse_maestro(maestro_path),
        catalog,
        _history(),
        manual_sha256=contract["manual_evidence"]["sha256"],
    )

    assert evidence["all_checks_pass"] is False
    assert evidence["checks"]["required_tests_and_current_bindings_match"] is False


def test_dbaccess_evidence_must_exist_and_be_nonempty(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(_DbAccessArtifactError, match="missing"):
        _load_oa_evidence(missing)

    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(_DbAccessArtifactError, match="empty"):
        _load_oa_evidence(empty)


def test_dbaccess_evidence_metadata_is_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "oa-evidence.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider": "unknown",
                "read_only": False,
                "cellviews": [{}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(_DbAccessArtifactError, match="provider=dbAccess"):
        _load_oa_evidence(path)


def test_dbaccess_evidence_contract_accepts_valid_payload(tmp_path: Path) -> None:
    path = tmp_path / "oa-evidence.json"
    payload = {
        "schema_version": 1,
        "provider": "dbAccess",
        "read_only": True,
        "cellviews": [{"library": "saradc", "cell": "comparator_new"}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert _load_oa_evidence(path) == payload


def test_history_rdb_maps_one_sided_spec_to_semantic_bound(tmp_path: Path) -> None:
    path = tmp_path / "history.rdb"
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE test(testID INTEGER, name TEXT);
            CREATE TABLE result(
                resultID INTEGER, testID INTEGER, name TEXT, resultOrder INTEGER
            );
            CREATE TABLE testStatus(testID INTEGER, statusCode INTEGER, errorID INTEGER);
            CREATE TABLE error(errorID INTEGER, message TEXT);
            CREATE TABLE spec(
                resultID INTEGER, type TEXT, expression1 TEXT, expression2 TEXT
            );
            CREATE TABLE point(pointID INTEGER);
            INSERT INTO test VALUES(1, 'noise');
            INSERT INTO result VALUES(10, 1, 'InputReferredNoise', 1);
            INSERT INTO spec VALUES(10, 'lt', '700u', '');
            """
        )
        connection.commit()
    finally:
        connection.close()

    parsed = parse_history_rdb(path)

    assert parsed["specifications"]["InputReferredNoise"] == {
        "type": "lt",
        "minimum": "",
        "maximum": "700u",
    }
