"""One method-aware check of observed source dependencies, shared by all consumers."""

from pathlib import Path

from .geometry import signature
from .jsonio import fail
from .project import dependencies


def observed(package, key, value, refs):
    row = package.document("device.json")["items"][key]
    lib = package.manifest["libraries"][row["library"]]
    root = package.roots.get(lib["root"])
    target = {"library": lib["name"], "cell": row["cell"], "view": row["view"]}
    if (
        value["target"] != target
        or not root
        or (Path(root) / lib["path"]).resolve() != Path(value["library"]["resolved_path"]).resolve()
    ):
        fail("Source library/device binding changed", "pdk_source_changed")
    current = dependencies(value, key)
    for ref in refs:
        declared = package.manifest["dependencies"][ref]
        kind = declared["kind"]
        if kind in {"file", "model"}:
            from .resource_check import check

            check(package, ref)
            continue
        if kind == "symbol":
            if declared["target"] != {**target, "library": row["library"]}:
                fail("Symbol dependency identity differs: " + ref, "pdk_source_changed")
            actual = signature(value, declared["method"])
        elif kind == 'interface' and declared['method'] == 'sico-cdf-siminfo-json-v1':
            # Legacy documentation ingests hashed the complete raw simInfo
            # envelope. Normalization retains its exact digest, including raw
            # issues/status that the usual projection can otherwise normalize.
            sim = value['simulators']
            if sim['status'] != 'complete' or not sim.get('raw_fingerprint') or declared['target'] != {
                    **target, 'library': row['library']}:
                fail('Legacy interface capture cannot be verified: ' + ref, 'pdk_source_changed')
            actual = sim['raw_fingerprint']
        else:
            candidate = current.get(ref, {})
            if (
                candidate.get("method") != declared["method"]
                or candidate.get("target") != declared["target"]
            ):
                fail("Unsupported source dependency: " + ref, "pdk_source_changed")
            actual = candidate.get("fingerprint")
        if actual != declared["fingerprint"]:
            fail("Source changed; review/rebase required: " + ref, "pdk_source_changed")
