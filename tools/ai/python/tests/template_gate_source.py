"""Read-only saved gate review with actual library identity, CDF and model polarity."""

import json
import re
from pathlib import Path

from circuit_create_support import AI_ROOT
from template_gate_labels import LIBRARY, MASTERS, MODEL_SHA256, classification
from template_real_review_support import (
    check_inventory,
    check_record,
    read_inventory,
    sha256,
    write_json,
)
from template_visual_support import export_schematic
from test_circuit_rc_gui import mcp_call

from cadai.template_catalog import TemplateCatalog
from cadai.template_source_qualification import require_qualified


def sources():
    report = AI_ROOT / "reference/circuit_templates/training/custom_ic.json"
    return json.loads(report.read_text())["source"]["resolved_libraries"]


def verify_models(bridge, model):
    assert sha256(model) == MODEL_SHA256
    deck = model.read_text()
    result = {}
    for cell, (name, kind) in MASTERS.items():
        cdf = json.loads(bridge.skill(
            'let((gscCdf) gscCdf=cdfGetCellCDF(ddGetObj(' + json.dumps(LIBRARY) + ' '
            + json.dumps(cell) + ')) aiCircuitJson(list('
            'cdfFindParamByName(gscCdf "model")~>defValue '
            "aiTplGet(aiTplGet(aiTplGet(gscCdf 'simInfo) 'spectre) 'termOrder))))"))
        assert cdf == [name, ["D", "G", "S", "B"]]
        start = deck.index("inline subckt " + name + " (D G S B)")
        block = deck[start:deck.index("ends " + name, start)]
        assert re.search(r"model\s+" + re.escape(name) + r"_x\s+bsim3v3\s+type\s*=\s*"
                         + {"nmos": "n", "pmos": "p"}[kind] + r"\b", block)
        result[cell] = dict(model=name, terminals=cdf[1], kind=kind)
    return result


def review_source(bridge, server, directory, case, library_path):
    directory.mkdir()
    oa = library_path / case["cell"] / "schematic/sch.oa"
    assert sha256(oa) == case["oa_sha256"]
    classes = {r.split()[0]: classification(r.split()[1]) for r in case["devices"]}
    extracted = mcp_call(server, "extract_circuit_templates", dict(
        library=case["library"], cell=case["cell"], assets=["schematic"],
        classifications=classes, reuse=dict(mode="whole", allow_rename=True)))
    assert extracted["ok"] and extracted["reuse"]["status"] == "qualified", extracted
    record = TemplateCatalog(workspace=directory.parent).get(extracted["template_ref"])
    require_qualified(record)
    # Keep a private evidence copy for the existing offline replay entry point.
    (directory / "capture.jsonl").write_bytes(Path(extracted["capture"]).read_bytes())
    write_json(directory / "extract.json", extracted)
    inventory = read_inventory(bridge, case)
    check_inventory(case, inventory, library=LIBRARY)
    check_record(case, record)
    visual = export_schematic(bridge, directory, dict(library=case["library"], cell=case["cell"],
                                                      view="schematic"), "source")
    assert sha256(oa) == case["oa_sha256"]
    for name, value in (("oa-inventory", inventory), ("record", record), ("visual", visual)):
        write_json(directory / (name + ".json"), value)
    return record
