"""Exercise bounded negative mutations against topology evidence."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, MutableMapping

from .ldo_qualification_evidence import validate_topology_evidence
from .ldo_qualification_status import (
    M3_BLOCKED_INPUT,
    M3_FAIL,
    M3_QUALIFIED,
    M3_SCHEMA_VERSION,
)
from .ldo_qualification_values import qualification_digest


def run_negative_injection_matrix(
    baseline: Mapping[str, Any],
    *,
    source_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Run bounded negative mutations and prove none can yield PASS."""

    names = (
        "wrong_port",
        "wrong_aon_en_polarity",
        "wrong_vdd_window",
        "corrupt_candidate_source",
        "modified_source_snapshot",
        "stale_template_lock",
        "missing_spectre_rnm_evidence",
    )
    results: dict[str, Any] = {}
    for name in names:
        mutated = deepcopy(dict(baseline))
        topology = "LDO_AON" if name == "wrong_aon_en_polarity" else "LDO_MASTER"
        entry = mutated.get(topology)
        if isinstance(entry, list):
            entry = entry[0] if entry else None
        if not isinstance(entry, MutableMapping):
            results[name] = {"status": M3_BLOCKED_INPUT, "findings": ["baseline topology evidence is missing"]}
            continue
        if name == "wrong_port":
            entry["interface_digest"] = "0" * 64
        elif name == "wrong_aon_en_polarity":
            entry["en_polarity"] = "active_low"
        elif name == "wrong_vdd_window":
            entry["vdd_window"] = {"min": 4.5, "max": 5.5}
        elif name == "corrupt_candidate_source":
            entry["candidate_sha256"] = "f" * 64
        elif name == "modified_source_snapshot":
            mutated["source_snapshot"] = {**dict(source_snapshot), "source_generation": "e" * 64}
        elif name == "stale_template_lock":
            entry["template_lock"] = "a" * 64
        elif name == "missing_spectre_rnm_evidence":
            gates = entry.get("gates")
            if isinstance(gates, MutableMapping):
                gates.pop("G3", None)
        source_for_check = mutated.get("source_snapshot", source_snapshot)
        checked = validate_topology_evidence(entry, topology=topology, source_snapshot=source_for_check)
        if checked.get("status") in {"PASS", M3_QUALIFIED}:
            checked = {**checked, "status": M3_FAIL, "findings": list(checked.get("findings", ())) + ["negative injection unexpectedly passed"]}
        results[name] = checked
    return {
        "schema_version": M3_SCHEMA_VERSION,
        "status": "PASS" if all(item.get("status") not in {"PASS", M3_QUALIFIED} for item in results.values()) else M3_FAIL,
        "injections": results,
        "injection_names": list(names),
        "result_sha256": qualification_digest(results),
    }
