from __future__ import annotations

import json
from pathlib import Path

from aivworkbench.m0s_inputs import parse_expand_config, stage_state, tree_identity
from aivworkbench.m0s_normalize import normalize_structure


CONFIG_TEXT = """\
config top;
design lib.top:schematic;
liblist lib, std;
viewlist verilogams, rtl, schematic;
stoplist spectre;
cell lib.leaf binding :verilogams;
inst (lib.top:schematic).u_leaf binding :verilogams;
inst (std.dff:functional).xinst0 binding :functional;
endconfig
"""


def test_state_is_staged_in_runams_load_directory_without_mutating_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source-state"
    source.mkdir()
    (source / "ADE_state.info").write_text("state\n", encoding="utf-8")
    (source / "environmentOptions.state").write_text("old options\n", encoding="utf-8")
    before = tree_identity((source,))

    record = stage_state(
        source,
        tmp_path / "state-root",
        library="lib",
        cell="top",
        simulator="ams",
        state_name="state1",
    )
    destination = tmp_path / "state-root" / "lib" / "top" / "ams" / "state1"
    (destination / "environmentOptions.state").write_text("migrated\n", encoding="utf-8")

    assert record["state_argument"] == f"{tmp_path / 'state-root'}:state1:ams"
    assert tree_identity((source,)) == before
    assert (source / "environmentOptions.state").read_text() == "old options\n"


def _write_netlist_evidence(root: Path, *, timestamp: str) -> Path:
    netlist = root / "netlist"
    (netlist / "digital" / "ihnl").mkdir(parents=True)
    (netlist / "digital" / "map").mkdir(parents=True)
    (netlist / ".binding.cfg").write_text(
        'config designtop="lib.top:schematic"\n'
        'config cell="leaf" lib="lib" view="verilogams"\n',
        encoding="utf-8",
    )
    (netlist / ".instBindInfoTable").write_text(
        '"u_leaf" ((("lib" "leaf" "verilogams" "verilogams") '
        '("lib" "top" "schematic" "schematic")))\n',
        encoding="utf-8",
    )
    (netlist / ".dbTermInfo").write_text('(\"a\")(\"b\")\n', encoding="utf-8")
    (netlist / "netlist.vams").write_text(
        f"// generated {timestamp}\nmodule top (a, b);\nendmodule\n",
        encoding="utf-8",
    )
    (netlist / "digital" / "ihnl" / "globalmap").write_text(
        "$model\nlib/top/schematic top\n$endmodel\n", encoding="utf-8"
    )
    (netlist / "digital" / "map" / "current").write_text(
        '(defbus ("bus" 3 0 "bus" 3 0))\n', encoding="utf-8"
    )
    (netlist / ".netlistStatus").write_text("success", encoding="utf-8")
    (netlist / "digital" / ".cellTblWithoutBind").write_text("", encoding="utf-8")
    log = root / "runams.log"
    log.write_text(
        f"Run date: {timestamp}\n"
        "Beginning netlist configuration information\n"
        "lib leaf verilogams *Stopping View*\n"
        "lib top schematic\n"
        "End of netlist configuration information\n"
        "INFO (AMS-1254): AMS UNL netlisting has completed successfully.\n",
        encoding="utf-8",
    )
    return log


def test_normalized_structure_is_stable_and_accounts_for_inactive_rules(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "expand.cfg"
    config_path.write_text(CONFIG_TEXT, encoding="utf-8")
    config = parse_expand_config(config_path)
    hed = tmp_path / "hed.json"
    hed.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider": "Virtuoso/HED",
                "config_opened": True,
                "top": {"library": "lib", "cell": "top", "view": "schematic"},
                "traversal": {"status": "OK", "node_count": 2},
            }
        ),
        encoding="utf-8",
    )
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider": "dbAccess",
                "authoritative": True,
                "libraries": [{"name": "lib", "read_path": "/oa/lib", "cells": []}],
                "top_schematic": {
                    "opened": True,
                    "terminals": [],
                    "instances": [
                        {
                            "name": "u_leaf",
                            "master": {"library": "lib", "cell": "leaf", "view": "symbol"},
                            "connections": [{"terminal": "a", "net": "a"}],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first_log = _write_netlist_evidence(first, timestamp="one")
    second_log = _write_netlist_evidence(second, timestamp="another timestamp")

    canonical_a, evidence_a = normalize_structure(
        hed_path=hed,
        catalog_path=catalog,
        config=config,
        runams_log=first_log,
        netlist_root=first / "netlist",
    )
    canonical_b, evidence_b = normalize_structure(
        hed_path=hed,
        catalog_path=catalog,
        config=config,
        runams_log=second_log,
        netlist_root=second / "netlist",
    )

    assert evidence_a["explicit_cell_bindings_preserved"] is True
    assert evidence_a["explicit_instance_bindings_accounted"] is True
    assert [item["instance"] for item in evidence_a["inactive_explicit_instance_bindings"]] == [
        "xinst0"
    ]
    assert evidence_a == evidence_b
    assert canonical_a == canonical_b
    assert canonical_a["normalized_sha256"] == canonical_b["normalized_sha256"]
