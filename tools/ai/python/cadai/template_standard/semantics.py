"""Cross-document graph, geometry, resource, and capability consistency checks."""

from pathlib import PurePosixPath

from ..template_schema import TemplateError
from ..template_topology import electrical_issues
from .codec import fail
from .topology import graph


def point(value):
    if not isinstance(value, list) or len(value) != 2 or any(type(v) is not int for v in value):
        fail("coordinates must be two integer DBU values")


def box(value):
    if not isinstance(value, list) or len(value) != 2:
        fail("invalid bounding box")
    for p in value:
        point(p)
    if any(value[0][k] > value[1][k] for k in (0, 1)):
        fail("inverted bounding box")


def validate(package, tables, docs):
    from .validate import keys

    dependencies = package["dependencies"]
    for ident, row in tables["file"]["items"].items():
        keys(row, "kind root path purpose", "dependency")
        path = PurePosixPath(row["path"])
        if path.is_absolute() or any(p in {"", ".", ".."} for p in row["path"].split("/")):
            fail("invalid resource path")
        if row["kind"] not in {"document", "image", "native_asset", "private_evidence", "other"}:
            fail("invalid resource kind")
        if "dependency" in row:
            dep = dependencies[row["dependency"]]
            if dep["target"].get("file_ref") != ident:
                fail("resource dependency must target its own file_ref")
    collection = docs["collection"]
    keys(collection["scope"], "level requested_assets")
    if collection["scope"]["level"] != "direct":
        fail("collection level must be direct")
    for name, row in collection["assets"].items():
        if name not in {"schematic", "symbol", "layout"}:
            fail("invalid requested asset")
        keys(row, "status dependencies observations", "reason")
        if row["status"] not in {"captured", "partial", "missing", "unavailable"}:
            fail("invalid asset status")
        if set(row["dependencies"]) - dependencies.keys():
            fail("missing asset dependency")
        from .content import OBSERVATIONS

        if set(row["observations"]) != set(OBSERVATIONS) or any(
            v not in {"present", "resolved", "absent", "unknown", "not_applicable"}
            for v in row["observations"].values()
        ):
            fail("invalid observation coverage")
        if (
            row["status"] != "captured" or "unknown" in row["observations"].values()
        ) and not row.get("reason"):
            fail("incomplete observation requires reason")
    top = docs.get("topology", {})
    for row in top.get("masters", {}).values():
        keys(row, "dependency", "pdk pdk_device")
        if row["dependency"] not in dependencies or ("pdk" in row) != ("pdk_device" in row):
            fail("invalid master dependency/PDK binding")
    for row in top.get("devices", {}).values():
        keys(row, "kind role master terminals", "source_name attributes")
    for row in top.get("nets", {}).values():
        keys(row, "name width scope signal_type")
    for row in top.get("ports", {}).values():
        keys(row, "name direction width")
    place = docs.get("placement")
    if place:
        frame = place["frame"]
        keys(frame, "unit dbu_per_uu anchor_method")
        if (
            frame["unit"] != "source_dbu"
            or type(frame["dbu_per_uu"]) is not int
            or frame["dbu_per_uu"] < 1
        ):
            fail("invalid placement frame")
        for key, row in place["instances"].items():
            keys(row, "xy orient bbox")
            if key not in top["devices"] or row["orient"] not in {
                "R0",
                "R90",
                "R180",
                "R270",
                "MX",
                "MY",
                "MXR90",
                "MYR90",
            }:
                fail("invalid instance identity/transform")
            point(row["xy"])
            box(row["bbox"])
        endpoints = {
            (r["device"], r["terminal"]) if "device" in r else ("port", r["port"])
            for r in top["connections"].values()
        }
        for row in place["anchors"].values():
            keys(row, "device terminal xy escape" if "device" in row else "port xy escape")
            endpoint = (
                (row["device"], row["terminal"]) if "device" in row else ("port", row["port"])
            )
            if endpoint not in endpoints or row["escape"] not in {
                "left",
                "right",
                "up",
                "down",
                "any",
                "unknown",
            }:
                fail("invalid anchor endpoint/escape")
            point(row["xy"])
    route = docs.get("routing")
    if route:
        if not place or route["placement_ref"] != "placement.json":
            fail("routing requires its placement")
        positions = {}
        for key, row in route["nodes"].items():
            keys(row, "anchors" if "anchors" in row else "xy")
            if "anchors" in row:
                anchors = row["anchors"]
                if not anchors or any(a not in place["anchors"] for a in anchors):
                    fail("missing node anchors")
                xy = place["anchors"][anchors[0]]["xy"]
                if any(place["anchors"][a]["xy"] != xy for a in anchors):
                    fail("node anchors are not coincident")
            else:
                xy = row["xy"]
            point(xy)
            positions[key] = xy
        for row in route["segments"].values():
            keys(row, "net from to", "width")
            if row["net"] not in top["nets"] or any(
                row[k] not in positions for k in ("from", "to")
            ):
                fail("invalid segment references")
            a, b = positions[row["from"]], positions[row["to"]]
            if a == b or (a[0] != b[0] and a[1] != b[1]):
                fail("segment must be nonzero orthogonal")
        for row in route["crossings"].values():
            keys(row, "segments relation", "node")
            if len(row["segments"]) != 2 or any(
                s not in route["segments"] for s in row["segments"]
            ):
                fail("invalid crossing segments")
            if row["relation"] not in {"joined", "isolated", "unknown"}:
                fail("invalid crossing relation")
            if row["relation"] == "joined":
                a, b = (route["segments"][s] for s in row["segments"])
                if a["net"] != b["net"] or row.get("node") not in (
                    {a["from"], a["to"]} & {b["from"], b["to"]}
                ):
                    fail("joined crossing requires a shared same-net endpoint")
            elif "node" in row:
                fail("only joined crossings may reference a node")
    for role in ("relation", "reuse"):
        for row in (
            docs.get(role, {}).get("items" if role == "relation" else "constraints", {}).values()
        ):
            keys(
                row, "kind devices" + (" basis" if role == "relation" else ""), "axis spacing_class"
            )
            if row["kind"] not in {"mirror", "row", "column", "alignment", "spacing"}:
                fail("unregistered relation/constraint")
            if len(row["devices"]) < 2 or set(row["devices"]) - top["devices"].keys():
                fail("invalid relation device references")
            if row["kind"] == "mirror" and len(row["devices"]) != 2:
                fail("mirror requires two devices")
    parameters = set()
    for row in docs.get("parameter", {}).get("items", {}).values():
        keys(row, "device name type value unit meaning role", "pdk_parameter")
        identity = (row["device"], row["name"])
        if row["device"] not in top["devices"] or identity in parameters:
            fail("invalid/duplicate parameter observation")
        parameters.add(identity)
    checks = docs["qualification"]["checks"]
    if checks["topology-v1"]["result"] == "passed":
        try:
            issues = electrical_issues(graph(top))
        except (TemplateError, KeyError):
            fail("passed topology has unsupported facts")
        obs = collection["assets"].get("schematic", {}).get("observations", {})
        # One shared gate: a verified inherited-connection default is resolved,
        # not unresolved (see qualification.ACCEPTED).
        from .qualification import ACCEPTED

        if issues or any(obs.get(k) not in ACCEPTED[k] for k in ACCEPTED):
            fail("passed topology has unresolved electrical facts")
    for capability in ("whole-reuse-v1", "core-reuse-v1", "placement-reference-v1"):
        if checks[capability]["result"] == "passed" and checks["topology-v1"]["result"] != "passed":
            fail("capability lacks passed topology prerequisite")
    if checks["placement-reference-v1"]["result"] == "passed":
        if not place or set(place["instances"]) != set(top["devices"]):
            fail("passed placement lacks device coverage")
        found = {
            (r["device"], r["terminal"]) if "device" in r else ("port", r["port"])
            for r in place["anchors"].values()
        }
        if endpoints != found:
            fail("passed placement lacks endpoint coverage")
    from .layout import validate as validate_layout
    from .route_checks import validate_receipt as validate_routes
    from .symbol_checks import validate_receipt as validate_symbol

    validate_layout(package, tables, docs)
    validate_routes(docs)
    from .versions import CURRENT, V2

    # v1 reference-only symbols retain their original reader/replay semantics.
    capture_version = tables["sources"]["items"].get("capture", {}).get("collector")
    validate_symbol(
        docs,
        sources=tables["sources"]["items"],
        strict=capture_version in {CURRENT, V2},
    )
    reuse = docs.get("reuse")
    if reuse:
        from .reuse import validate_reuse

        validate_reuse(package, top, reuse)
    for mode in ("whole", "core"):
        if checks[mode + "-reuse-v1"]["result"] == "passed" and (
            not reuse or reuse["mode"] != mode
        ):
            fail("passed reuse does not match contract")
