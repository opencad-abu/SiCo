"""One method-aware check of observed source dependencies, shared by all consumers.

``outcome`` inspects one declared dependency of one observed device and reports
its status plus the live fingerprint when computable; ``compare`` wraps the
binding and refs of a whole capture, ``observed`` is the fail-closed gate built
on the same comparison, and the refresh rebase reads the same live fingerprints
instead of growing a second opinion about what counts as a changed source.
"""

from pathlib import Path

from .geometry import signature
from .jsonio import fail, fingerprint
from .project import dependencies


def binding(package, key, value):
    """The observed device's library/view/path binding against its declared target."""
    row = package.document("device.json")["items"][key]
    lib = package.manifest["libraries"][row["library"]]
    root = package.roots.get(lib["root"])
    target = {"library": lib["name"], "cell": row["cell"], "view": row["view"]}
    if (value["target"] != target or not root
            or (Path(root) / lib["path"]).resolve() != Path(value["library"]["resolved_path"]).resolve()):
        return {"status": "changed", "detail": "Source library/device binding changed"}
    return {"status": "ok", "detail": None}


def outcome(package, key, value, ref, binding_status="ok"):
    """One dependency's live comparison: {ref, kind, status, detail, fingerprint}.

    status is ok/changed for comparable dependencies, or identity/unverifiable/
    unsupported/binding_changed when the declared record itself cannot be held
    against the live capture. fingerprint carries the live value when known.
    """
    declared = package.manifest["dependencies"][ref]
    kind = declared["kind"]
    if kind in {"file", "model"}:
        from .resource_check import status as resource_status

        res = resource_status(package, ref)
        return {"ref": ref, "kind": kind, "status": res["status"],
                "detail": res["detail"], "fingerprint": res.get("actual")}
    row = package.document("device.json")["items"][key]
    lib = package.manifest["libraries"][row["library"]]
    target = {"library": lib["name"], "cell": row["cell"], "view": row["view"]}
    if binding_status != "ok":
        return {"ref": ref, "kind": kind, "status": "binding_changed",
                "detail": "Source library/device binding changed", "fingerprint": None}
    if kind == "symbol":
        if declared["target"] != {**target, "library": row["library"]}:
            return {"ref": ref, "kind": kind, "status": "identity",
                    "detail": "Symbol dependency identity differs: " + ref, "fingerprint": None}
        live = signature(value, declared["method"])
    elif kind == 'interface' and declared['method'] == 'sico-cdf-siminfo-json-v1':
        # Legacy documentation ingests hashed the complete raw simInfo envelope.
        # Normalization retains its exact digest, including raw issues/status
        # that the usual projection can otherwise normalize.
        sim = value['simulators']
        if sim['status'] != 'complete' or not sim.get('raw_fingerprint') or declared['target'] != {
                **target, 'library': row['library']}:
            return {"ref": ref, "kind": kind, "status": "unverifiable",
                    "detail": 'Legacy interface capture cannot be verified: ' + ref,
                    "fingerprint": None}
        live = sim['raw_fingerprint']
    elif kind == 'interface' and declared['method'] == 'sico-siminfo-builtin-v1':
        # The builtin projection binds the captured simInfo items directly.
        sim = value['simulators']
        if sim['status'] != 'complete' or declared['target'] != {
                **target, 'library': row['library']}:
            return {"ref": ref, "kind": kind, "status": "unverifiable",
                    "detail": 'Builtin interface capture cannot be verified: ' + ref,
                    "fingerprint": None}
        live = fingerprint(sim['items'])
    else:
        candidate = dependencies(value, key).get(ref, {})
        if (candidate.get("method") != declared["method"]
                or candidate.get("target") != declared["target"]):
            return {"ref": ref, "kind": kind, "status": "unsupported",
                    "detail": "Unsupported source dependency: " + ref, "fingerprint": None}
        live = candidate.get("fingerprint")
    if live != declared["fingerprint"]:
        return {"ref": ref, "kind": kind, "status": "changed",
                "detail": "Source changed; review/rebase required: " + ref,
                "fingerprint": live}
    return {"ref": ref, "kind": kind, "status": "ok", "detail": None, "fingerprint": live}


def compare(package, key, value, refs):
    """Non-raising outcomes: {binding: {status, detail}, refs: [{ref, kind, status, detail}]}."""
    state = binding(package, key, value)
    current = dependencies(value, key)
    rows = [outcome(package, key, value, ref, state["status"]) for ref in refs]
    return {"binding": state, "refs": rows}


def observed(package, key, value, refs):
    outcome = compare(package, key, value, refs)
    if outcome["binding"]["status"] != "ok":
        fail(outcome["binding"]["detail"], "pdk_source_changed")
    for row in outcome["refs"]:
        if row["status"] != "ok":
            fail(row["detail"], "pdk_source_changed")
