"""Licensed real-source optional-group omission and inherited-connection boundary."""

import os
from pathlib import Path

import pytest
from template_optional_labels import INVERTER, LABEL_SHA256, OPAMP, label_digest
from template_optional_replay import omission_request, replay
from template_optional_source import optional_record, review_source
from template_pdk_support import bind_device, create, pdk_session, readback
from template_real_labels import MASTERS, classification
from template_real_review_support import read_inventory, reusable, sha256, source_info, write_json
from template_visual_support import export_schematic
from test_circuit_rc_gui import mcp_call

from cadai.template_reuse_build import publish_reusable
from cadai.template_schema import TemplateError

pytestmark = pytest.mark.skipif(
    os.environ.get("SICO_AI_RUN_GUI_TESTS") != "1", reason="licensed real optional-group review"
)


def test_real_series_compensation_omission(tmp_path, monkeypatch):
    assert label_digest() == LABEL_SHA256
    sources = source_info()
    pdk = Path(sources["gpdk045"])
    model = pdk.parent / "models/spectre/gpdk045_mos.scs"
    paths = [
        Path(sources[c["library"]]) / c["cell"] / "schematic/sch.oa" for c in (OPAMP, INVERTER)
    ]
    if not model.is_file() or not all(p.is_file() for p in paths):
        pytest.skip("authorized saved source/model unavailable")
    before = {str(p): sha256(p) for p in paths}
    with pdk_session(tmp_path, pdk) as (bridge, server):
        with (tmp_path / "cds.lib").open("a") as stream:
            for case in (OPAMP, INVERTER):
                stream.write(f"DEFINE {case['library']} {sources[case['library']]}\n")
        assert bridge.skill("ddUpdateLibList()") == "t"
        bases = {
            c["id"]: review_source(
                bridge, tmp_path / c["id"], c, Path(sources[c["library"]]), model
            )
            for c in (OPAMP, INVERTER)
        }
        with pytest.raises(TemplateError, match=INVERTER["rejection"]):
            reusable(bases["inverter"])
        record = optional_record(OPAMP, bases["opamp"])
        write_json(tmp_path / "opamp/record.json", record)
        replayed = replay(record)
        write_json(tmp_path / "replay.json", replayed)
        package = tmp_path / "review-package"
        catalog = package / "catalogs/optional.sqlite3"
        publish_reusable([record], catalog)
        package_hash = sha256(catalog)
        monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(package))
        cells = sorted({r.split()[1] for r in OPAMP["devices"]})
        bound = {
            cell: bind_device(
                server,
                cell,
                ["l", "fingers", "w", "m"],
                dict(
                    G="left",
                    B="right",
                    D="up" if MASTERS[cell][1] == "nmos" else "down",
                    S="down" if MASTERS[cell][1] == "nmos" else "up",
                ),
                classification=classification(cell),
            )
            for cell in cells
        }
        write_json(tmp_path / "bindings.json", bound)
        query = mcp_call(server, "query_circuit_templates", dict(tier="project"))
        assert [r["template_ref"] for r in query["templates"]] == [record["template_ref"]]
        request, audit = omission_request(record, bound)
        write_json(tmp_path / "request.json", request)
        write_json(tmp_path / "adaptation.json", audit)
        prepared = mcp_call(server, "prepare_circuit_template_plan", request)
        write_json(tmp_path / "prepare.json", prepared)
        assert prepared["status"] == "ready_for_creation_prepare", prepared
        plan = prepared["plan"]
        assert plan["template_use"] == audit["use"]
        preview = plan["geometry"]
        ports = preview["plan"]["ports"]
        assert len(ports) == 6 and all(p["placement_source"] == "template" for p in ports)
        wires = [w for w in preview["plan"]["wires"] if "port" in w["endpoint"]]
        assert len(wires) == 6 and all(len(w["points"]) == 2 and w.get("label") for w in wires)
        args = dict(
            spec=plan["spec"],
            bindings=plan["preview"]["plan"]["bindings"],
            template_use=plan["template_use"],
            preview_digest=plan["preview"]["preview_digest"],
            geometry=plan["geometry_input"],
            layout=preview["plan"]["layout"],
            geometry_plan_digest=preview["geometry_plan_digest"],
        )
        result = create(server, args, "real-optional")
        result["physical_readback"] = readback(bridge, plan["spec"], preview["plan"]["wires"])
        target = plan["spec"]["target"]
        inventory = read_inventory(bridge, target)
        actual = {r[0]: dict(r[4]) for r in inventory[0] if r[1] != "basic"}
        assert actual == {i["name"]: i["connections"] for i in plan["spec"]["instances"]}
        assert len(actual) == 8 and not {"U_C0", "U_R2"} & actual.keys()
        assert "N_R2_MINUS" not in {r[0] for r in inventory[2]}
        write_json(tmp_path / "target-inventory.json", inventory)
        result["visual"] = export_schematic(bridge, tmp_path, target, "target")
        assert before == {str(p): sha256(p) for p in paths}
        assert sha256(catalog) == package_hash
        write_json(
            tmp_path / "acceptance.json",
            dict(
                labels_digest=label_digest(),
                review="codex_review; human_expert_pending; no_stability_or_performance_claim",
                source_oa=before,
                source_unchanged=True,
                package_sha256=package_hash,
                inverter_rejection=INVERTER["rejection"],
                replay=replayed,
                result=result,
            ),
        )
