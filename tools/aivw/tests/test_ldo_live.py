from __future__ import annotations

import json
from pathlib import Path

from aivworkbench.ldo_live import _catalog_summary, _hed_summary, _runams_summary
from aivworkbench.ldo_normalize import build_official_ldo_binding, normalize_ldo_structure
from aivworkbench.ldo_qualification import build_source_snapshot
from aivworkbench.process import ProcessResult


def test_catalog_summary_requires_both_read_only_ldo_topologies() -> None:
    report, findings = _catalog_summary(
        {
            "schema_version": 1,
            "provider": "dbAccess",
            "authoritative": True,
            "read_only": True,
            "library": "amsLDO",
            "cells": [
                {
                    "name": "LDO_MASTER",
                    "views": ["schematic", "symbol", "config", "verilogams"],
                    "schematic": {
                        "opened": True,
                        "terminal_count": 3,
                        "instance_count": 15,
                        "terminals": [
                            {"name": "VDD"},
                            {"name": "VSS"},
                            {"name": "VOUT"},
                        ],
                    },
                }
            ],
        }
    )
    assert report["cells"][0]["cell"] == "LDO_MASTER"
    assert "catalog did not return both LDO topologies" in findings


def test_hed_summary_checks_config_and_top_identity() -> None:
    summary, findings = _hed_summary(
        {
            "schema_version": 1,
            "provider": "Virtuoso/HED",
            "authority": "authenticated-cdns-ipc-read-only",
            "config_opened": True,
            "config": {"library": "amsLDO", "cell": "LDO_AON", "view": "config"},
            "top": {"library": "amsLDO", "cell": "LDO_AON", "view": "schematic"},
            "traversal": {"status": "OK"},
            "formal_saved_state": True,
            "double_read_equal": True,
            "source_generation": "a" * 64,
        },
        "LDO_AON",
    )
    assert summary["traversal_ok"] is True
    assert findings == []


def test_hed_summary_accepts_the_pre_rename_authority() -> None:
    """Records written before the cdns-ipc rename must still authenticate."""

    summary, findings = _hed_summary(
        {
            "schema_version": 1,
            "provider": "Virtuoso/HED",
            "authority": "authenticated-virtuoso-ipc-read-only",
            "config_opened": True,
            "config": {"library": "amsLDO", "cell": "LDO_AON", "view": "config"},
            "top": {"library": "amsLDO", "cell": "LDO_AON", "view": "schematic"},
            "traversal": {"status": "OK"},
            "formal_saved_state": True,
            "double_read_equal": True,
            "source_generation": "a" * 64,
        },
        "LDO_AON",
    )
    assert summary["authenticated"] is True
    assert findings == []


def test_hed_summary_rejects_an_unknown_authority() -> None:
    summary, findings = _hed_summary(
        {
            "schema_version": 1,
            "provider": "Virtuoso/HED",
            "authority": "untrusted-read-only",
            "config_opened": True,
            "config": {"library": "amsLDO", "cell": "LDO_AON", "view": "config"},
            "top": {"library": "amsLDO", "cell": "LDO_AON", "view": "schematic"},
            "traversal": {"status": "OK"},
            "formal_saved_state": True,
            "double_read_equal": True,
            "source_generation": "a" * 64,
        },
        "LDO_AON",
    )
    assert summary["authenticated"] is False
    assert "authority" in " ".join(findings)


def test_runams_summary_is_bounded_and_detects_fatal_log(tmp_path: Path) -> None:
    root = tmp_path / "runams"
    (root / "netlist").mkdir(parents=True)
    (root / "netlist" / ".netlistStatus").write_text("failed\n", encoding="utf-8")
    log = tmp_path / "runams.log"
    log.write_text("*F, AMS-1 fatal\n", encoding="utf-8")
    result = ProcessResult(
        command=("runams",),
        cwd=str(tmp_path),
        started_at="now",
        finished_at="now",
        elapsed_seconds=0.1,
        returncode=1,
        timed_out=False,
        log_file=str(log),
    )
    summary = _runams_summary(root, log, result)
    assert summary["fatal_log_marker"] is True
    assert summary["netlist_status"] == "failed"
    json.dumps(summary)


def test_runams_summary_counts_official_binding_tables(tmp_path: Path) -> None:
    root = tmp_path / "runams" / "netlist"
    root.mkdir(parents=True)
    (root / ".netlistStatus").write_text("success\n", encoding="utf-8")
    (root / ".binding.cfg").write_text(
        'config designtop="amsLDO.LDO_MASTER:schematic"\n'
        'config cell="g45n2svt" view="analogtext"\n',
        encoding="utf-8",
    )
    (root / ".instBindInfoTable").write_text(
        '"M0" ((("gpdk045" "pmos2v" "spectre" "spectre") '
        '("amsLDO" "LDO_MASTER" "schematic" "schematic")))\n',
        encoding="utf-8",
    )
    log = tmp_path / "runams.log"
    log.write_text("AMS UNL netlisting has completed successfully\n", encoding="utf-8")
    result = ProcessResult(
        command=("runams",), cwd=str(tmp_path), started_at="now", finished_at="now",
        elapsed_seconds=0.1, returncode=0, timed_out=False, log_file=str(log),
    )
    summary = _runams_summary(tmp_path / "runams", log, result)
    assert summary["binding_count"] == 1
    assert summary["instance_binding_count"] == 1


def test_ldo_normalizer_resolves_implicit_library_and_staged_rule(tmp_path: Path) -> None:
    payload = tmp_path
    netlist = payload / "runams" / "netlist"
    (netlist / "digital").mkdir(parents=True)
    (netlist / "digital" / ".cellTblWithoutBind").write_text("", encoding="utf-8")
    for name in (".instBindInfoTable", ".dbTermInfo", "netlist.vams", "digital/ihnl/globalmap", "digital/map/current"):
        path = netlist / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("evidence\n", encoding="utf-8")
    (netlist / ".netlistStatus").write_text("success\n", encoding="utf-8")
    (netlist / ".binding.cfg").write_text(
        'config designtop="amsLDO.LDO_MASTER:schematic"\n'
        'config cell="g45n2svt" view="analogtext"\n', encoding="utf-8"
    )
    (netlist / ".bindedCellToLVTable").write_text(
        '"g45n2svt" ("gpdk045" "spectre")\n', encoding="utf-8"
    )
    (netlist / ".instBindInfoTable").write_text(
        '"M0" ((("gpdk045" "nmos2v" "spectre" "spectre") ("amsLDO" "LDO_MASTER" "schematic" "schematic")))\n',
        encoding="utf-8",
    )
    (payload / "connect-rules").mkdir()
    (payload / "connect-rules" / "CR_full_fast.vams").write_text(
        "connectrules CR_full_fast; endconnectrules\n", encoding="utf-8"
    )
    (netlist / "xrunArgs").write_text(
        "-discipline logic\n-amsconnrules CR_full_fast\n", encoding="utf-8"
    )
    (payload / "hed-LDO_MASTER.json").write_text(json.dumps({
        "schema_version": 1, "provider": "Virtuoso/HED", "config_opened": True,
        "top": {"library": "amsLDO", "cell": "LDO_MASTER", "view": "schematic"},
        "traversal": {"status": "OK"},
    }), encoding="utf-8")
    (payload / "catalog.json").write_text(json.dumps({
        "schema_version": 1, "provider": "dbAccess", "authoritative": True, "read_only": True,
        "cells": [{"name": "LDO_MASTER", "schematic": {"opened": True, "terminal_count": 3, "instance_count": 1}}],
    }), encoding="utf-8")
    log = payload / "runams.log"
    log.write_text("AMS UNL netlisting has completed successfully\n", encoding="utf-8")
    normalized, evidence = normalize_ldo_structure(
        cell="LDO_MASTER", hed_path=payload / "hed-LDO_MASTER.json", catalog_path=payload / "catalog.json",
        runams_root=payload / "runams", payload_root=payload, runams_log=log,
    )
    assert normalized["ams_unl"]["bindings"][0]["library"] == "gpdk045"
    assert evidence["connect_rule_evidence"] is True


def test_official_ldo_binding_requires_source_preflight_pass(tmp_path: Path) -> None:
    with __import__("pytest").raises(Exception, match="PASS source preflight"):
        build_official_ldo_binding(
            cell="LDO_MASTER", payload_root=tmp_path, source_generation="a" * 64,
            source_preflight={"status": "BLOCKED_INPUT"},
        )


def test_source_snapshot_resolves_approved_cds_include_graph(tmp_path: Path) -> None:
    source = tmp_path / "source"
    mapping = tmp_path / "mapping"
    model = source / "models" / "spectre"
    for cell in ("LDO_MASTER", "LDO_AON"):
        for relative in (
            "schematic/sch.oa",
            "symbol/symbol.oa",
            "config/expand.cfg",
            "verilogams/verilog.vams",
        ):
            path = source / "amsLDO" / cell / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("source\n", encoding="utf-8")
    model.mkdir(parents=True)
    (model / "gpdk045.scs").write_text(
        "section tt\nendsection\nsection ff\nendsection\nsection ss\nendsection\n",
        encoding="utf-8",
    )
    (source / "cds.lib").write_text("DEFINE amsLDO ./amsLDO\n", encoding="utf-8")
    for name in ("gpdk045", "analogLib", "basic"):
        (mapping / name).mkdir(parents=True, exist_ok=True)
    (mapping / "included.lib").write_text(
        "DEFINE gpdk045 ./gpdk045\n"
        "DEFINE analogLib ${SICO_LIB_ROOT}/analogLib\n"
        "DEFINE basic $SICO_LIB_ROOT/basic\n",
        encoding="utf-8",
    )
    (mapping / "cds.lib").write_text(
        "INCLUDE \"included.lib\"\nDEFINE amsLDO %s\n" % (source / "amsLDO"),
        encoding="utf-8",
    )
    snapshot = build_source_snapshot(
        source,
        cds_lib=mapping / "cds.lib",
        source_cds_lib=source / "cds.lib",
        mapping_root=tmp_path,
        mapping_environment={"SICO_LIB_ROOT": str(mapping)},
    )
    assert snapshot["status"] == "BLOCKED_ENVIRONMENT"
    assert snapshot["cds_lib"]["mapping"]["missing"] == []
    assert snapshot["cds_lib"]["mapping"]["definitions"]["analogLib"]["resolved"] is True
    assert snapshot["cds_lib"]["source"]["path"] == "cds.lib"
    target_snapshot = build_source_snapshot(
        source, cells=("LDO_MASTER",), scope="recipe_target",
        cds_lib=mapping / "cds.lib", source_cds_lib=source / "cds.lib",
        mapping_root=tmp_path, mapping_environment={"SICO_LIB_ROOT": str(mapping)},
    )
    assert target_snapshot["errors"] == []
    assert target_snapshot["scope"] == "recipe_target"
    assert target_snapshot["source_generation"] != snapshot["source_generation"]
    family_snapshot = build_source_snapshot(
        source, cells=("LDO_MASTER",), cds_lib=mapping / "cds.lib",
        source_cds_lib=source / "cds.lib", mapping_root=tmp_path,
        mapping_environment={"SICO_LIB_ROOT": str(mapping)},
    )
    assert "source snapshot must include exactly LDO_MASTER and LDO_AON" in family_snapshot["errors"]
