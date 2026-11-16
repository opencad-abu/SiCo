"""Qualification needs full net attachment; geometric proximity never grants contact."""

import pytest
from template_qualification_fixture import collect_rows, routed_rows, wire

from cadai.template_schema import TemplateError
from cadai.template_standard.route_checks import issues, validate_receipt


def test_full_paths_split_t_contacts_and_keep_anchor_authority(tmp_path):
    result, item = collect_rows(tmp_path, routed_rows())
    docs = item["documents"]
    assert result["capabilities"]["routing-reference-v1"] == "passed"
    assert issues(docs) == []
    route = docs["routing"]
    assert len(route["segments"]) == 10  # Each rail splits at its port branch.
    assert {r["relation"] for r in route["crossings"].values()} == {"joined"}
    assert all(("anchors" in n) != ("xy" in n) for n in route["nodes"].values())
    assert {a for n in route["nodes"].values() for a in n.get("anchors", [])} == set(
        docs["placement"]["anchors"]
    )


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("unobserved", "wire_ownership_unverified"),
        ("open", "open_source_wire_end"),
        ("disconnected", "source_net_disconnected"),
        ("overlap", "overlapping_source_wire"),
        ("off_net", "different_source_nets_touch"),
        ("body", "source_wire_crosses_device_body"),
        ("diagonal", "non_orthogonal_source_wire"),
        ("multiple", "missing_or_multiple_endpoint_anchors"),
    ],
)
def test_incomplete_paths_stay_investigable(tmp_path, fault, reason):
    rows = routed_rows()
    shapes = [r for r in rows if r["kind"] == "shape"]
    if fault == "unobserved":
        rows[1].pop("wire_net_semantics")
    elif fault == "open":
        rows.insert(-2, wire("A", [[-3, 10], [-3, 12]]))
    elif fault == "disconnected":
        rows.remove(shapes[2])
    elif fault == "overlap":
        rows.insert(-2, wire("A", [[-3, 2], [-3, 8]]))
    elif fault == "off_net":
        rows.insert(-2, wire("A", [[-3, 5], [5, 5]]))
    elif fault == "body":
        # Valid but deliberately broad observed body encloses part of both rails.
        for r in rows:
            if r["kind"] == "instance_geometry":
                r["observation"]["occupied_bbox"] = [[-4, -1], [4, 1]]
    elif fault == "diagonal":
        rows.insert(-2, wire("A", [[-3, 0], [-4, 2]]))
    else:
        next(r for r in rows if r["kind"] == "instance_geometry")["observation"]["pin_centers"][0][
            1
        ].append([1, 0.5])
    result, item = collect_rows(tmp_path, rows)
    assert result["capabilities"]["routing-reference-v1"] == "unknown"
    assert reason in {g["reason"] for g in item["documents"]["collection"]["gaps"].values()}


@pytest.mark.parametrize(
    "fault", ["wrong_net", "missing_crossing", "false_isolated", "lost_anchor"]
)
def test_passed_receipt_is_rechecked(tmp_path, fault):
    _, item = collect_rows(tmp_path, routed_rows())
    docs = item["documents"]
    route = docs["routing"]
    if fault == "wrong_net":
        route["segments"]["s0"]["net"] = "n1"
    elif fault == "missing_crossing":
        route["crossings"].pop(next(iter(route["crossings"])))
    elif fault == "false_isolated":
        next(iter(route["crossings"].values()))["relation"] = "isolated"
    else:
        node = next(n for n in route["nodes"].values() if "anchors" in n)
        anchor = node.pop("anchors")[0]
        node["xy"] = docs["placement"]["anchors"][anchor]["xy"]
    with pytest.raises(TemplateError, match="passed routing"):
        validate_receipt(docs)


def crossing_rows():
    rows = routed_rows()
    rows = [r for r in rows if not (r["kind"] == "shape" and r.get("net") == "Y")]
    next(r for r in rows if r["kind"] == "port_anchor" and r["terminal"] == "Y")["xy"] = [-6, 5]
    rows.insert(
        -2, wire("Y", [[1, 0], [2, 0], [2, 3], [-6, 3], [-6, 13], [3, 13], [3, 10], [1, 10]])
    )
    return rows


@pytest.mark.parametrize("marker", [False, True])
def test_only_unmarked_observed_interior_x_is_isolated(tmp_path, marker):
    rows = crossing_rows()
    if marker:
        rows.insert(
            -2,
            dict(
                kind="shape",
                net="A",
                figure=dict(
                    objType="ellipse", lpp=["wire", "drawing"], bBox=[[-3.1, 2.9], [-2.9, 3.1]]
                ),
            ),
        )
    result, item = collect_rows(tmp_path, rows)
    assert result["capabilities"]["routing-reference-v1"] == ("unknown" if marker else "passed")
    crossings = item["documents"]["routing"]["crossings"].values()
    assert ("unknown" if marker else "isolated") in {r["relation"] for r in crossings}


def test_interior_same_net_x_cannot_gain_junction_qualification(tmp_path):
    rows = routed_rows()
    rows.insert(-2, wire("A", [[-4, 2], [-2, 2]]))
    result, item = collect_rows(tmp_path, rows)
    assert result["capabilities"]["routing-reference-v1"] == "unknown"
    reasons = {g["reason"] for g in item["documents"]["collection"]["gaps"].values()}
    assert "ambiguous_source_crossing" in reasons


def test_distinct_same_device_terminals_keep_shared_physical_anchor(tmp_path):
    rows = routed_rows()
    next(r for r in rows if r["kind"] == "instance")["terminal_names"].append("B")
    next(r for r in rows if r["kind"] == "instance_geometry")["observation"]["pin_centers"].append(
        ["B", [[-1, 0]]]
    )
    next(r for r in rows if r["kind"] == "net" and r["name"] == "A")["instance_terminal_count"] += 1
    rows.insert(-2, dict(kind="instance_terminal", instance="M0", name="B", net="A"))
    result, item = collect_rows(tmp_path, rows)
    assert result["capabilities"]["routing-reference-v1"] == "passed"
    docs = item["documents"]
    shared = [
        n["anchors"] for n in docs["routing"]["nodes"].values() if len(n.get("anchors", [])) == 2
    ]
    assert len(shared) == 1
    assert {docs["placement"]["anchors"][a]["terminal"] for a in shared[0]} == {"G", "B"}


def test_coincident_different_net_anchors_never_merge(tmp_path):
    rows = routed_rows()
    next(r for r in rows if r["kind"] == "instance_geometry")["observation"]["pin_centers"][0][
        1
    ] = [[-1, 0]]
    result, item = collect_rows(tmp_path, rows)
    assert result["capabilities"]["routing-reference-v1"] == "unknown"
    docs = item["documents"]
    nets = {
        (r.get("device"), r.get("terminal"), r.get("port")): r["net"]
        for r in docs["topology"]["connections"].values()
    }
    for node in docs["routing"]["nodes"].values():
        anchors = [docs["placement"]["anchors"][a] for a in node.get("anchors", [])]
        assert (
            len({nets[(a.get("device"), a.get("terminal"), a.get("port"))] for a in anchors}) <= 1
        )
