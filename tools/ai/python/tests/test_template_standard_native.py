"""Native source identities, layout capability, and direct Binder projection boundaries."""

import copy
import json

import pytest
from template_native_content_fixture import guard, layout_rows
from template_qualification_fixture import collect_rows, routed_rows

from cadai.template_capture import parse_capture
from cadai.template_native_content import METHOD, fingerprint
from cadai.template_schema import TemplateError, canonical
from cadai.template_standard.replay import replay


def mutate(rows, change):
    for row in rows:
        if "native_content" in row:
            change(row["native_content"])
        if "native_content_after" in row:
            change(row["native_content_after"])


def test_static_master_fingerprint_uses_bytes_interface_not_path(tmp_path):
    rows = guard(routed_rows())
    result, item = collect_rows(tmp_path, rows)
    dependencies = item["package"]["dependencies"]
    assert all(
        dependencies[k]["method"] == METHOD and isinstance(dependencies[k]["fingerprint"], str)
        for k in item["documents"]["topology"]["masters"]
    )
    view = rows[1]["native_content"]["views"][1]
    assert fingerprint(view) == fingerprint(view | dict(library_path="/other"))
    changed = copy.deepcopy(view)
    changed["files"][0]["sha256"] = "0" * 64
    assert fingerprint(view) != fingerprint(changed)
    changed = copy.deepcopy(view)
    changed["terminals"][0]["direction"] = "input"
    assert fingerprint(view) != fingerprint(changed)
    assert "/pdk" not in json.dumps(dependencies)
    assert replay(item)[3]["collector"] == "sico-capture-flat-v3"
    assert result["capabilities"]["routing-reference-v1"] == "passed"


@pytest.mark.parametrize(
    "field,value", [("parameterized", True), ("instance_count", 1), ("mosaic_count", 1)]
)
def test_nonstatic_master_stays_unknown(tmp_path, field, value):
    rows = guard(routed_rows())
    mutate(rows, lambda c: c["views"][1].update({field: value}))
    _, item = collect_rows(tmp_path, rows)
    deps = [d for d in item["package"]["dependencies"].values() if d["kind"] == "master"]
    assert any(isinstance(d["fingerprint"], dict) for d in deps)


def test_parameterized_master_interface_stays_per_instance(tmp_path):
    rows = guard(routed_rows())
    # The saved interface of a parameterized master is one evaluation; instances
    # may observe different terminal sets and must not be cross-checked against it.
    mutate(rows, lambda c: [v for v in c["views"] if v["role"] == "master"][0].update(
        parameterized=True, terminals=[]))
    _, item = collect_rows(tmp_path, rows)
    deps = [d for d in item["package"]["dependencies"].values() if d["kind"] == "master"]
    assert any(isinstance(d["fingerprint"], dict) for d in deps)


@pytest.mark.parametrize("schematic,bindings", [(True, True), (True, False), (False, False)])
def test_layout_reference_independent_of_binding_presence(tmp_path, schematic, bindings):
    result, item = collect_rows(tmp_path, layout_rows(schematic=schematic, bindings=bindings))
    layout = item["documents"]["layout"]
    assert result["capabilities"]["layout-reference-v1"] == "passed"
    assert layout["binding_status"] == ("verified" if bindings else "unavailable")
    if bindings:
        assert {d for r in layout["bindings"].values() for d in r["devices"]} == {"d0", "d1"}
        assert all(
            p.startswith("oa-inst:") for r in layout["bindings"].values() for p in r["objects"]
        )
    replay(item)


@pytest.mark.parametrize("fault", ["mismatch", "coverage", "interface", "digest", "duplicate"])
def test_native_evidence_corruption_rejected(fault):
    rows = guard(routed_rows())
    if fault == "mismatch":
        rows[-2]["native_content_after"]["views"][0]["files"][0]["sha256"] = "0" * 64
    else:

        def change(c):
            if fault == "coverage":
                c["views"].pop()
            elif fault == "interface":
                c["views"][1]["terminals"].pop()
            elif fault == "digest":
                c["views"][0]["files"][0]["sha256"] = "mtime:123"
            else:
                c["views"].append(copy.deepcopy(c["views"][0]))

        mutate(rows, change)
    with pytest.raises(TemplateError, match="native"):
        parse_capture(("\n".join(canonical(r) for r in rows) + "\n").encode())


def test_unknown_layout_dependency_does_not_gain_qualification(tmp_path):
    rows = layout_rows()
    mutate(
        rows,
        lambda c: [
            v.update(parameterized=True)
            for v in c["views"]
            if v["target"] == ["pdk", "mos", "layout"]
        ],
    )
    result, item = collect_rows(tmp_path, rows)
    assert result["capabilities"]["layout-reference-v1"] == "unknown"
    assert item["documents"]["layout"]["binding_status"] == "verified"


def test_partial_binding_keeps_unmapped_device(tmp_path):
    rows = layout_rows()
    rows = [r for r in rows if not (r["kind"] == "binding" and r["instance"] == "P1")]
    result, item = collect_rows(tmp_path, guard(rows))
    assert result["capabilities"]["layout-reference-v1"] == "passed"
    assert item["documents"]["layout"]["binding_status"] == "partial"


def test_binder_context_conflict_cannot_certify_mapping(tmp_path):
    rows = layout_rows()
    context = next(r for r in rows if r["kind"] == "layout_context")
    context["source"]["cell"] = "OTHER"
    _, item = collect_rows(tmp_path, guard(rows))
    assert item["documents"]["layout"]["binding_status"] == "unavailable"
    assert item["documents"]["layout"]["bindings"] == {}


def test_many_to_many_binder_group_preserves_all_members(tmp_path):
    rows = layout_rows()
    binds = [r for r in rows if r["kind"] == "binding"]
    logical = [r["objects"][0] for r in binds]
    for row in binds:
        row["objects"] = copy.deepcopy(logical)
        peer = "P1" if row["instance"] == "P0" else "P0"
        row["siblings"] = [
            [dict(object_type="inst", name=peer, library="demo", cell="INV", view="layout")]
        ]
    _, item = collect_rows(tmp_path, guard(rows))
    layout = item["documents"]["layout"]
    assert layout["binding_status"] == "verified" and len(layout["bindings"]) == 1
    group = layout["bindings"]["b0"]
    assert group["devices"] == ["d0", "d1"] and len(group["objects"]) == 2


def test_missing_native_observation_keeps_layout_unknown(tmp_path):
    rows = layout_rows()
    for row in rows:
        row.pop("native_content", None)
        row.pop("native_content_after", None)
    result, item = collect_rows(tmp_path, rows)
    assert result["capabilities"]["layout-reference-v1"] == "unknown"
    assert item["documents"]["layout"]["binding_status"] == "verified"


def test_reader_rechecks_layout_identity_and_binding_coverage(tmp_path):
    from cadai.template_standard.layout import validate

    _, item = collect_rows(tmp_path, layout_rows())
    docs = item["documents"]
    docs["layout"]["bindings"].pop("b1")
    with pytest.raises(TemplateError, match="complete device coverage"):
        validate(item["package"], item["tables"], docs)
    docs["layout"]["binding_status"] = "partial"
    item["package"]["dependencies"]["layout"]["fingerprint"] = dict(state="unknown", reason="lost")
    with pytest.raises(TemplateError, match="incomplete native content"):
        validate(item["package"], item["tables"], docs)


def test_physical_member_subset_never_proves_whole_instance_coverage(tmp_path):
    rows = layout_rows()
    bind = next(r for r in rows if r["kind"] == "binding")
    bind["siblings"] = [
        [dict(object_type="inst", name="P1", library="demo", cell="INV", view="layout"), 2]
    ]
    _, item = collect_rows(tmp_path, guard(rows))
    assert item["documents"]["layout"]["binding_status"] == "partial"
    assert any(
        "member=2" in p
        for row in item["documents"]["layout"]["bindings"].values()
        for p in row["objects"]
    )
