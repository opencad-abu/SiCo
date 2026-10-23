"""Project only observed direct Binder groups; never infer layout devices from geometry."""

from urllib.parse import quote

from .codec import fail


def project(record, docs):
    asset = record["assets"]["layout"]
    layout = docs["layout"]
    logical = {r["source_name"]: r["id"] for r in record.get("topology", {}).get("devices", [])}
    contexts = asset.get("contexts", [])
    origin = docs["collection"]["origin"]
    expected = [origin["library"], origin["cell"], origin["views"].get("schematic")]
    context = contexts[0] if len(contexts) == 1 else {}
    observed = context.get("source") or {}
    if (
        not logical
        or context.get("binding_status") != "query_available_unverified"
        or [observed.get(k) for k in ("library", "cell", "view")] != expected
        or context.get("conn_ref") not in (None, ["CELLVIEW", *expected, ""])
    ):
        return
    physical, mapped = set(), set()
    member_subset = False
    for group in asset.get("binding_groups", []):
        devices = sorted({logical[m["object"]["name"]] for m in group["logical_members"]})
        # Hierarchical/vector members stay in raw evidence until whole-device coverage exists.
        if any(
            m[k] is not None for m in group["logical_members"] for k in ("member", "row", "column")
        ):
            continue
        objects = []
        for row in group["physical_members"]:
            physical.add(row["object"]["name"])
            member_subset |= any(row[k] is not None for k in ("member", "row", "column"))
            objects.append(
                "oa-inst:"
                + quote(row["object"]["name"], safe="")
                + "?"
                + "&".join(
                    k + "=" + (str(row[k]) if row[k] is not None else "all")
                    for k in ("member", "row", "column")
                )
            )
        layout["bindings"]["b" + str(len(layout["bindings"]))] = dict(
            devices=devices,
            objects=sorted(set(objects)),
            evidence_ref="capture:" + record["capture_sha256"],
        )
        mapped.update(devices)
    if layout["bindings"]:
        complete = (
            asset["coverage"]["binding_status"] == "observed_direct_instances"
            and not asset["binding_gaps"]
            and not member_subset
            and mapped == set(logical.values())
            and physical == {r["name"] for r in asset["instances"]}
        )
        layout["binding_status"] = "verified" if complete else "partial"


def validate(package, tables, docs):
    from .validate import keys

    layout = docs.get("layout")
    if not layout:
        return
    keys(
        layout, "source depends_on asset dependency binding_status bindings", "evidence bbox frame"
    )
    if layout["binding_status"] not in {"verified", "partial", "unavailable"}:
        fail("invalid layout binding status")
    resource = tables["file"]["items"].get(layout["asset"], {})
    dependency = package["dependencies"].get(layout["dependency"], {})
    if resource.get("kind") != "native_asset" or dependency.get("kind") != "layout":
        fail("layout requires a native resource and layout dependency")
    if "dependency" in resource and resource["dependency"] != layout["dependency"]:
        fail("layout resource identity differs")
    devices = docs.get("topology", {}).get("devices", {})
    covered, objects = set(), set()
    for row in layout["bindings"].values():
        keys(row, "devices objects evidence_ref")
        if (
            not isinstance(row["devices"], list)
            or not row["devices"]
            or set(row["devices"]) - devices.keys()
            or len(set(row["devices"])) != len(row["devices"])
            or not isinstance(row["objects"], list)
            or not row["objects"]
            or any(not isinstance(p, str) or not p for p in row["objects"])
            or len(set(row["objects"])) != len(row["objects"])
            or covered.intersection(row["devices"])
            or objects.intersection(row["objects"])
            or not isinstance(row["evidence_ref"], str)
            or not row["evidence_ref"]
        ):
            fail("invalid/overlapping layout binding group")
        covered.update(row["devices"])
        objects.update(row["objects"])
    if layout["binding_status"] == "verified" and (not covered or covered != set(devices)):
        fail("verified layout binding lacks complete device coverage")
    if layout["binding_status"] == "unavailable" and layout["bindings"]:
        fail("unavailable layout binding cannot declare groups")
    if docs["qualification"]["checks"]["layout-reference-v1"]["result"] == "passed":
        from .native import layout_issues

        if resource.get("dependency") != layout["dependency"] or layout_issues(
            docs, package["dependencies"]
        ):
            fail("passed layout has incomplete native content identity")
