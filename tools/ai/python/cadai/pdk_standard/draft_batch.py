"""Restrict draft batches to reviewed usage and parameter-policy decisions."""

from copy import deepcopy

from .jsonio import fail, fingerprint
from .patches import apply, node, tokens
from .provenance import confirmed

POLICY = {"write", "requirement", "write_when", "required_when"}


def validate_changes(package, entries, changes):
    devices = package.document("device.json")["items"]
    mapped = {}
    usage = set()
    for row in entries:
        if row["role"] == "device":
            usage.add(row["device"])
        else:
            mapped.setdefault(devices[row["device"]]["dir"] + "/cdf.json", set()).add(row["cdf"])
    seen, reused, pending = set(), [], []
    files = set()
    for change in changes:
        name, operations = change["file"], change["ops"]
        paths = [op["path"] for op in operations]
        if name in files or any(
            a == b or a.startswith(b + "/") or b.startswith(a + "/")
            for i, a in enumerate(paths)
            for b in paths[i + 1 :]
        ):
            fail("Batch must use distinct files and nonoverlapping policy paths")
        files.add(name)
        document = package.document(name)
        apply(document, operations)  # Existence and JSON-pointer checks also cover no-op reuse.
        active = []
        for op in operations:
            parts = tokens(op["path"])
            if name == "device.json":
                valid = (
                    len(parts) >= 3
                    and parts[0] == "items"
                    and parts[1] in usage
                    and (parts[2] == "reason" or len(parts) == 4 and parts[2] == "use")
                )
                key = (name, parts[1]) if valid else None
            else:
                valid = (
                    name in mapped
                    and len(parts) >= 3
                    and parts[0] == "parameters"
                    and parts[1] in mapped[name]
                    and parts[2] in POLICY
                )
                key = (name, parts[1]) if valid else None
                if name in mapped and parts == ["interface_parameters"]:
                    before = set(document.get("interface_parameters", []))
                    after = set(op.get("value", [])) if op["op"] != "remove" else set()
                    valid = (before ^ after) <= mapped[name]
                    key = None
            if not valid:
                fail(
                    "Batch patch must target mapped policy fields; import document facts separately"
                )
            if key:
                seen.add(key)
            if (
                op["op"] == "replace"
                and node(document, op["path"]) == op["value"]
                and confirmed(package, document, op["path"])
            ):
                reused.append({"file": name, "path": op["path"], "value": deepcopy(op["value"])})
            else:
                active.append(op)
        if active:
            pending.append({"file": name, "ops": active})
    expected = {(name, key) for name, keys in mapped.items() for key in keys}
    expected |= {("device.json", key) for key in usage}
    if not expected <= seen:
        fail("Each selected mapping must have an exact policy patch")
    return pending, reused


def review(state, entries, batch_id, reused):
    return {
        "draft_ref": state["draft_ref"],
        "batch_id": batch_id,
        "source": state["source_identity"],
        "entries": [{k: v for k, v in row.items() if k != "source_signature"} for row in entries],
        "reused": reused,
    }


def request_digest(args):
    return fingerprint({key: args[key] for key in ("batch_id", "entry_ids", "changes")})
