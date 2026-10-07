"""Source identities and observed form labels for private manual-mapping drafts."""

from copy import deepcopy

from ..pdk_normalize import context, detail
from .jsonio import fail, fingerprint


def manual(package, source):
    if "unavailable_reason" in source:
        return {"state": "unavailable", "reason": source["unavailable_reason"]}
    ref = source["file_ref"]
    row = package.document("file.json")["items"].get(ref)
    if not row or row.get("kind") != "document" or "dependency" not in row:
        fail(
            "Manual must reference a registered document with a byte dependency",
            "pdk_source_changed",
        )
    from .resource_check import check

    check(package, row["dependency"])
    dep = package.manifest["dependencies"][row["dependency"]]
    return {
        "state": "verified",
        "file_ref": ref,
        "path": row["path"],
        "document_revision": source["document_revision"],
        "fingerprint": dep["fingerprint"],
        "dependency": row["dependency"],
    }


def signature(package, entry):
    row = package.document("device.json")["items"].get(entry["device"])
    if row is None:
        fail("Unknown PDK device in draft", "device_unavailable")
    identity = {key: row.get(key) for key in ("library", "cell", "view", "dir")}
    deps = {}
    for suffix in ("cdf.json", "symbol.json", "simulation.json"):
        name = row.get("dir", "") + "/" + suffix
        if package.available(name):
            for ref in package.document(name)["depends_on"]:
                dep = package.manifest["dependencies"][ref]
                if dep["kind"] not in {"file", "model"}:
                    deps[ref] = dep
    if "cdf" in entry:
        name = row.get("dir", "") + "/cdf.json"
        if not package.available(name) or entry["cdf"] not in package.document(name)["parameters"]:
            fail("Draft CDF name is not present in the observed device", "pdk_source_changed")
    return fingerprint({"identity": identity, "dependencies": deps})


def observe(package, entries, bridge, evidence=None):
    """Read saved OA metadata, checking its fingerprints before accepting form labels."""
    from .source_check import observed

    devices = package.document("device.json")["items"]
    values = {}
    for key in sorted({row["device"] for row in entries}):
        row = devices.get(key)
        if row is None:
            fail("Unknown PDK device in draft", "device_unavailable")
        selected = [item for item in entries if item["device"] == key]
        if not any("cdf" in item for item in selected):
            continue
        if bridge is None:
            fail("Parameter mapping requires an observed CDF source", "pdk_source_unavailable")
        target = {
            "library": package.manifest["libraries"][row["library"]]["name"],
            "cell": row["cell"],
            "view": row["view"],
        }
        capture = bridge.capture("device", target)
        if evidence:
            from ..pdk_data import PdkData

            PdkData(evidence["workspace"], {}).write(evidence["root"] / (key + ".json"), capture)
        value = detail(capture["data"], context(capture["context"]))
        if (value.get("master_state") or {}).get("modified") is not False:
            fail("Save the PDK source master before mapping", "pdk_source_changed")
        refs = package.document(row["dir"] + "/cdf.json")["depends_on"]
        observed(package, key, value, refs)
        values[key] = {p["name"]: p.get("prompt") for p in value["parameters"]["items"]}
    result = []
    for entry in entries:
        row = deepcopy(entry)
        row["source_signature"] = signature(package, entry)
        if "cdf" in row:
            if row["cdf"] not in values[row["device"]]:
                fail("CDF name disappeared from live source", "pdk_source_changed")
            label = values[row["device"]][row["cdf"]]
            row["form_label"] = label if isinstance(label, str) else {"state": "unknown"}
        result.append(row)
    return result
