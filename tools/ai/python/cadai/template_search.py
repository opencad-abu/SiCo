"""Offline exhaustive exact-search baseline over a bounded, frozen topology pool.

No catalog publication snapshot, binding resolver, cache or OA calls are implied.
The first VF2 mapping is evidence of existence only; it never creates a ready plan.
"""

from __future__ import annotations

import copy
import time

from .circuit_spec_schema import CircuitSpecError, array, canonical, digest, mapping, obj, validate
from .template_graph import MATCHER_VERSION as MATCHER_VERSION
from .template_graph import match
from .template_prepare_schema import (
    DEFAULTS,
    POLICY,
    TIERS,
    VERSION,
    issue,
    search_options,
    serialize_result,
)
from .template_ranking import ranking_key
from .template_schema import NAME, REF, RULE_VERSION, TemplateError, TemplateUnavailable
from .template_topology import evidence_gaps, topology_parts

POOL_ITEM = obj(
    {
        "template_ref": REF,
        "topology": obj({}, (), additionalProperties=True),
        "origins": {**array({"type": "string", "enum": list(TIERS)}, 3, 1), "uniqueItems": True},
    }
)
PORT_MAPS = mapping(mapping(NAME, NAME, 128), REF, 10000)
MAX_POOL_BYTES = 32 * 1024 * 1024


def _pool(values):
    if not isinstance(values, list) or len(values) > 10000:
        raise TemplateError("baseline pool must contain at most 10000 entries")
    records, size = {}, 0
    for value in values:
        validate(value, POOL_ITEM, "pool entry")
        size += len(canonical(value).encode())
        if size > MAX_POOL_BYTES:
            raise TemplateError("baseline pool exceeds 32 MiB")
        ref = value["template_ref"]
        if ref in records:
            if canonical(records[ref]["topology"]) != canonical(value["topology"]):
                raise TemplateUnavailable("immutable template reference conflict: " + ref)
            records[ref]["origins"] = sorted(set(records[ref]["origins"] + value["origins"]))
        else:
            records[ref] = copy.deepcopy(value)
        records[ref]["origins"].sort(key=TIERS.index)
    return [records[ref] for ref in sorted(records)]


def _candidate(item, result, allowed_tiers):
    matched = result["status"] == "matched"
    graph_map = result.get("mapping") if matched else None
    return {
        "template_ref": item["template_ref"],
        "origins": item["origins"],
        "eligibility": "eligible" if matched else "inconclusive",
        "hard_checks": ["typed_whole_graph_isomorphism"] if matched else [],
        "mapping": graph_map,
        "rank_features": {
            "adaptation_cost": 0,
            "target_coverage": 1.0,
            "style_penalty": 0,
            "origin_priority": min(
                TIERS.index(tier) for tier in item["origins"] if tier in allowed_tiers
            ),
            "mapping_digest": digest(graph_map),
        }
        if matched
        else None,
        "requirements": ["mapping_confirmation", "target_binding", "target_geometry"]
        if matched
        else [result.get("reason", "matcher_unknown")],
    }


def _port_maps(values, pool, target):
    validate(values, PORT_MAPS, "port_maps")
    if set(values) - {v["template_ref"] for v in pool}:
        raise TemplateError("port_maps contains an absent or excluded template reference")
    target_names = {p["name"] for p in target["ports"]}
    for item in pool:
        rename = values.get(item["template_ref"], {})
        if not rename:
            continue
        try:
            topology_parts(item["topology"])
        except TemplateError as exc:
            raise TemplateUnavailable("invalid source topology: " + str(exc)) from exc
        names = {p["name"] for p in item["topology"]["ports"]}
        if set(rename) - names or set(rename.values()) - target_names:
            raise TemplateError("port_map refers to absent ports")
        if len({rename.get(n, n) for n in names}) != len(names):
            raise TemplateError("port_map is not one-to-one")


def search_exact(
    target,
    candidates,
    options=None,
    *,
    template_ref=None,
    port_maps=None,
    clock=time.monotonic,
    matcher=match,
):
    """Check every in-scope candidate, sharing time/states and retaining unknowns.

    Inputs are already materialized offline data. Pool acquisition is measured by
    the replay runner separately. No top-k filter or fingerprint proof is used.
    """
    started = clock()
    opts = copy.deepcopy(DEFAULTS)
    snapshot = None
    pool, eligible, unknown = [], [], []
    examined, states, trace = 0, 0, []

    def finish(status, reason, issues):
        ranked = sorted(eligible, key=ranking_key)
        choices = ranked or unknown
        remaining = len(pool) - examined
        elapsed = max(0.0, (clock() - started) * 1000)
        if status in {"needs_mapping", "no_match"} and elapsed >= opts["max_query_time_ms"]:
            status, reason = "inconclusive", "time_budget"
            issues = [issue(reason, "Selection exceeded the query deadline"), *issues]
        complete = reason == "exhausted" and not remaining and not unknown
        result = {
            "schema": VERSION,
            "status": status,
            "snapshot_ref": snapshot,
            "search": {
                "complete": complete,
                "selection_mode": opts["selection_mode"],
                "examined": examined,
                "remaining": remaining,
                "stop_reason": reason,
            },
            "best_match": choices[0] if choices else None,
            "alternatives": choices[1 : 1 + opts["return_alternatives"]],
            "plan": None,
            "issues": issues[:64],
            "diagnostics": {
                "elapsed_ms": elapsed,
                "budget_overrun_ms": max(0.0, elapsed - opts["max_query_time_ms"]),
                "states_used": states,
                "unresolved": len(unknown),
                "trace_omitted": max(0, examined - len(trace)),
                "trace": trace,
                "cache": "disabled",
                "index": "disabled",
                "versions": {
                    "matcher": MATCHER_VERSION,
                    "normalizer": RULE_VERSION,
                    "ranking": POLICY,
                    "snapshot_kind": "offline_pool.v1",
                },
                "budget": {
                    "query_ms": opts["max_query_time_ms"],
                    "match_states": opts["max_match_states"],
                },
            },
        }
        try:
            return serialize_result(result)
        except TemplateError as exc:
            if str(exc) != "prepare result exceeds response budget":
                raise
            result.update(
                status="unavailable",
                best_match=None,
                alternatives=[],
                issues=[issue("response_budget", "Candidate maps exceed response budget")],
            )
            result["search"].update(complete=False, stop_reason="response_budget")
            return serialize_result(result)

    try:
        opts = search_options(options, template_ref)
        # The legacy exact API is intentionally strict.  Core matching and
        # whole-prepare budgets belong to the P2 facade; accepting them here
        # would silently route a request through the wrong verifier.
        supplied = options or {}
        if opts["match_mode"] != "exact" or "max_prepare_time_ms" in supplied:
            raise TemplateError("search_exact supports match_mode=exact only")
        parts = topology_parts(target)
        if sum(len(p) for p in parts) > 512:
            raise TemplateError("target graph exceeds 512 nodes")
        target = copy.deepcopy(target)
        target_gaps = evidence_gaps(target)
        pool = _pool(candidates)
        # Origin provenance participates in selection; location never changes template identity.
        snapshot = "offline_" + digest(pool)
        if not pool:
            return finish(
                "unavailable", "no_catalog_data", [issue("no_catalog_data", "Empty pool")]
            )
        if template_ref is not None and template_ref not in {v["template_ref"] for v in pool}:
            return finish(
                "unavailable",
                "reference_unavailable",
                [issue("reference_unavailable", "Fixed reference is absent", template_ref)],
            )
        pool = [
            v
            for v in pool
            if set(v["origins"]) & set(opts["allowed_tiers"])
            and (template_ref is None or v["template_ref"] == template_ref)
        ]
        if template_ref is not None and not pool:
            return finish(
                "unavailable",
                "reference_excluded",
                [issue("reference_excluded", "Fixed reference is outside allowed tiers")],
            )
        port_maps = {} if port_maps is None else port_maps
        _port_maps(port_maps, pool, target)
    except (CircuitSpecError, TemplateError) as exc:
        return finish(
            "invalid_request", "invalid_request", [issue("invalid_request", str(exc)[:500])]
        )
    except TemplateUnavailable as exc:
        return finish(
            "unavailable", "pool_unavailable", [issue("pool_unavailable", str(exc)[:500])]
        )

    deadline = started + opts["max_query_time_ms"] / 1000
    issues, stop = [], "exhausted"
    for item in pool:
        remaining_time = deadline - clock()
        remaining_states = opts["max_match_states"] - states
        if remaining_time <= 0 or remaining_states <= 0:
            stop = "time_budget" if remaining_time <= 0 else "state_budget"
            break
        try:
            gaps = sorted(set(evidence_gaps(item["topology"]) + target_gaps))
            if gaps:
                result = {"status": "inconclusive", "reason": "incomplete_electrical_evidence"}
                issues.append(issue(result["reason"], ", ".join(gaps)[:500], item["template_ref"]))
            else:
                remaining_time = deadline - clock()
                if remaining_time <= 0:
                    stop = "time_budget"
                    break
                result = matcher(
                    item["topology"],
                    target,
                    port_maps.get(item["template_ref"], {}),
                    timeout=min(0.1, remaining_time),
                    max_states=remaining_states,
                )
                spent = result.get("states", 0)
                if type(spent) is not int or not 0 <= spent <= remaining_states + 1:
                    raise TemplateUnavailable("matcher returned invalid state accounting")
        except TemplateError as exc:
            return finish(
                "unavailable",
                "invalid_topology",
                [issue("invalid_topology", str(exc)[:500], item["template_ref"])],
            )
        except TemplateUnavailable as exc:
            return finish(
                "unavailable", "matcher_unavailable", [issue("matcher_unavailable", str(exc)[:500])]
            )
        examined += 1
        states += result.get("states", 0)
        if len(trace) < 32:
            trace.append(
                {
                    "template_ref": item["template_ref"],
                    "outcome": result["status"],
                    "reason": result.get("reason"),
                }
            )
        if result["status"] == "matched":
            eligible.append(_candidate(item, result, opts["allowed_tiers"]))
        elif result["status"] == "inconclusive":
            unknown.append(_candidate(item, result, opts["allowed_tiers"]))
            if not gaps:
                issues.append(
                    issue(
                        result.get("reason", "matcher_unknown"),
                        "Exact comparison remains unresolved",
                        item["template_ref"],
                    )
                )
        elif result["status"] != "different":
            raise TemplateError("invalid matcher result status")
        if clock() >= deadline or states > opts["max_match_states"]:
            stop = "time_budget" if clock() >= deadline else "state_budget"
            break
        if eligible and opts["selection_mode"] == "first_eligible":
            stop = "first_eligible"
            break

    if clock() >= deadline:
        stop = "time_budget"
    if stop in {"time_budget", "state_budget"} or (unknown and stop != "first_eligible"):
        status = "inconclusive"
        if stop == "exhausted":
            stop = "unresolved_candidates"
        issues.insert(0, issue(stop, "Search cannot establish a complete ranking"))
    elif eligible:
        status = "needs_mapping"
        issues.extend(
            [
                issue("mapping_confirmation", "First VF2 mapping is not proof of unique mapping"),
                issue("target_binding", "Target bindings have not been resolved", stage="binding"),
                issue("target_geometry", "Target geometry has not been prepared", stage="geometry"),
            ]
        )
    else:
        status = "no_match"
    return finish(status, stop, issues)
