"""Read-only real-source review and fixed-label comparison; no alternate matcher."""

import hashlib
import json
import re

from circuit_create_support import AI_ROOT
from template_real_labels import MODEL_SHA256, classification, connections
from template_visual_support import export_schematic

from cadai.template_build import make_template
from cadai.template_capture import read_capture
from cadai.template_reuse_build import make_reusable
from cadai.template_rules import rule_ref


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_info():
    return json.loads((AI_ROOT / "reference/circuit_templates/training/ams_gui.json").read_text())[
        "source"]["resolved_libraries"]


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def read_inventory(bridge, case):
    identity = " ".join(json.dumps(case[k]) for k in ("library", "cell"))
    return json.loads(bridge.skill(
        'let((rriCv rriRows) rriCv=dbOpenCellViewByType(' + identity + ' "schematic" "" "r") '
        'rriRows=aiCircuitJson(list(foreach(mapcar i rriCv~>instances '
        'list(i~>name i~>libName i~>cellName i~>viewName '
        'foreach(mapcar it i~>instTerms list(it~>name it~>net~>name)))) '
        'foreach(mapcar p rriCv~>terminals list(p~>name p~>direction p~>net~>name)) '
        'foreach(mapcar n rriCv~>nets list(n~>name n~>sigType n~>isGlobal)))) '
        'dbClose(rriCv) rriRows)'))


def check_inventory(case, inventory, *, library="gpdk045"):
    devices, ports, nets = inventory
    electrical = {r[0]: r for r in devices if r[1] != "basic"}
    assert {n: dict(r[4]) for n, r in electrical.items()} == connections(case)
    expected_masters = {r.split()[0]: r.split()[1] for r in case["devices"]}
    assert {n: (r[1], r[2], r[3]) for n, r in electrical.items()} == {
        n: (library, m, "symbol") for n, m in expected_masters.items()}
    assert sorted(ports) == sorted([r.split() + [r.split()[0]] for r in case["ports"]])
    assert {n: (typ, global_) for n, typ, global_ in nets} == {
        n: ("supply" if n == case["supply"] else "ground" if n == case["ground"]
            else "signal", None) for pins in connections(case).values() for n in pins.values()}


def check_record(case, record):
    top = record["topology"]
    names = {n["id"]: n["source_name"] for n in top["nets"]}
    assert {d["source_name"]: {p["name"]: names[p["net"]] for p in d["pins"]}
            for d in top["devices"]} == connections(case)
    assert sorted((p["name"], p["direction"], names[p["net"]]) for p in top["ports"]) == sorted(
        (r.split()[0], r.split()[1], r.split()[0]) for r in case["ports"])


def verified_classifications(bridge, case, model_path):
    from template_real_labels import MASTERS

    assert sha256(model_path) == MODEL_SHA256
    deck = model_path.read_text()
    cdf_rows = {}
    for cell in sorted({row.split()[1] for row in case["devices"]}):
        row = json.loads(bridge.skill(
            'let((rrcCdf) rrcCdf=cdfGetCellCDF(ddGetObj("gpdk045" ' + json.dumps(cell) + ')) '
            'aiCircuitJson(list(cdfFindParamByName(rrcCdf "model")~>defValue '
            "aiTplGet(aiTplGet(aiTplGet(rrcCdf 'simInfo) 'spectre) 'termOrder))))"))
        model, kind = MASTERS[cell]
        assert row == [model, ["D", "G", "S", "B"]]
        start = deck.index("inline subckt " + model + " ")
        block = deck[start:deck.index("\nends " + model, start)]
        polarity = re.search(r"model\s+\w+\s+bsim4\s*\{\s*1:type=([np])", block)
        assert polarity and polarity[1] == {"nmos": "n", "pmos": "p"}[kind]
        cdf_rows[cell] = dict(model=model, terminals=row[1], polarity=polarity[1])
    classes = {row.split()[0]: classification(row.split()[1]) for row in case["devices"]}
    return classes, cdf_rows


def reusable(record):
    devices = record["topology"]["devices"]
    contract = dict(core_devices=[d["id"] for d in devices],
                    core_connections=[dict(device=d["id"], terminal=p["name"])
                                      for d in devices for p in d["pins"]],
                    boundary_terminals=[], optional_groups=[],
                    allowed_rule_refs=[rule_ref("rename")], placement_constraints=[],
                    routing_reference=dict(asset="schematic", status="unavailable",
                                           anchor_ids=[], segment_ids=[]))
    classes = {d["id"]: d["classification_evidence"] for d in devices}
    return make_reusable(record, contract, classes)


def review_source(bridge, directory, case, source_path, model_path):
    directory.mkdir()
    oa = source_path / case["cell"] / "schematic/sch.oa"
    assert sha256(oa) == case["oa_sha256"]
    capture_path = directory / "capture.jsonl"
    bridge.skill('aiTplCapture(' + ' '.join(json.dumps(case[k]) for k in ("library", "cell"))
                 + ' "schematic" "symbol" "layout" ' + json.dumps(str(capture_path))
                 + ' list("schematic") "cad.template.capture.v2")')
    inventory = read_inventory(bridge, case)
    check_inventory(case, inventory)
    classes, cdf = verified_classifications(bridge, case, model_path)
    record = make_template(read_capture(capture_path), classifications=classes)
    check_record(case, record)
    qualified = reusable(record)
    visual = export_schematic(bridge, directory, dict(library=case["library"], cell=case["cell"],
                                                      view="schematic"), "source")
    assert sha256(oa) == case["oa_sha256"]
    for name, value in (("oa-inventory", inventory), ("cdf-evidence", cdf),
                        ("record", qualified), ("visual", visual)):
        write_json(directory / (name + ".json"), value)
    return qualified
