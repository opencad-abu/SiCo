"""Bounded core candidate selection using the common prepare and ranking contracts."""

import math
import time

from .template_core_match import match_core
from .template_prepare_schema import TIERS, issue, search_options, serialize_result
from .template_prepare_schema import VERSION as PREPARE_VERSION
from .template_ranking import ranking_key
from .template_schema import TemplateError, canonical, digest

VERSION = "20260922.core-search.v2"


def _core_result(status, reason, *, started, states=0, examined=0, remaining=0,
                 snapshot_ref=None, best=None, alternatives=None, candidates=None,
                 issues=None, timeout=0.5, max_states=50000, unresolved=0,
                 complete=False, stop_reason=None, selection_mode="best", clock=time.monotonic):
    """Build and validate the shared prepare envelope for every core branch."""
    elapsed = max(0.0, (clock() - started) * 1000)
    stop_reason = stop_reason or reason or ("exhausted" if complete else "budget_exhausted")
    result = {
        "schema": PREPARE_VERSION,
        "status": status,
        "snapshot_ref": snapshot_ref,
        "search": {
            "complete": complete,
            "selection_mode": selection_mode,
            "examined": examined,
            "remaining": remaining,
            "stop_reason": stop_reason,
        },
        "best_match": best,
        "alternatives": list(alternatives or []),
        "plan": None,
        "issues": list(issues or []),
        "diagnostics": {
            "elapsed_ms": elapsed,
            "budget_overrun_ms": max(0.0, elapsed - timeout * 1000),
            "states_used": states,
            "unresolved": unresolved,
            "trace_omitted": max(0, examined - 32),
            "cache": "disabled",
            "index": "disabled",
            "versions": {"matcher": VERSION, "prepare": PREPARE_VERSION},
            "budget": {"query_ms": timeout * 1000, "match_states": max_states},
            "trace": [],
        },
        # Compatibility aliases are bounded and never the authority for
        # selection; new callers consume best_match/alternatives/search.
        "candidates": list(candidates or [])[:8],
        "states": states,
        "reason": reason,
    }
    return serialize_result(result)


def search_core(target, candidates, *, timeout=0.5, max_states=50000,
                port_map=None, device_map=None, terminal_map=None, net_map=None,
                omitted_groups=(), selection_mode="best", return_alternatives=2,
                clock=time.monotonic):
    """Select within the shared budget; first_eligible never claims global ranking."""
    started, states, rows = clock(), 0, []
    pool, stopped_early = {}, False
    options = search_options()
    try:
        options = search_options({"selection_mode": selection_mode,
                                  "return_alternatives": return_alternatives})
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise TemplateError("invalid core search budget")
        if type(max_states) is not int or not 1 <= max_states <= 200000:
            raise TemplateError("invalid core search budget")
        if not isinstance(candidates, list) or len(candidates) > 10000:
            raise TemplateError("core pool must contain at most 10000 candidates")
        if len(canonical(candidates).encode()) > 32 * 1024 * 1024:
            raise TemplateError("core pool exceeds 32 MiB")
        for record in candidates:
            if not isinstance(record, dict) or not isinstance(record.get("template_ref"), str):
                raise TemplateError("core pool contains a malformed record")
            ref = record["template_ref"]
            if ref in pool and pool[ref] != record:
                raise TemplateError("immutable template reference conflict")
            pool[ref] = record
        if not pool:
            return _core_result(
                "unavailable", "no_catalog_data", started=started, snapshot_ref=None,
                issues=[issue("no_catalog_data", "Empty core candidate pool")],
                timeout=timeout, max_states=max_states, selection_mode=selection_mode, clock=clock,
            )
        for ref, record in sorted(pool.items()):
            left = timeout - (clock() - started)
            if left <= 0 or states >= max_states:
                break
            row = match_core(
                record, target, timeout=left, max_states=max_states - states,
                port_map=port_map, device_map=device_map, terminal_map=terminal_map,
                net_map=net_map, omitted_groups=omitted_groups, clock=clock
            )
            states += row["states"]
            rows.append({"template_ref": ref, **row})
            if (selection_mode == "first_eligible" and row["complete"]
                    and row["status"] in {"matched", "needs_mapping"}
                    and clock() - started < timeout and states <= max_states):
                stopped_early = True
                break
    except (KeyError, TypeError, ValueError, TemplateError) as exc:
        return _core_result(
            "invalid_request", "invalid_core_pool", started=started, states=states,
            issues=[issue("invalid_core_pool", str(exc))], timeout=timeout,
            max_states=max_states, selection_mode=options["selection_mode"], clock=clock,
        )
    complete = not stopped_early and len(rows) == len(pool) and all(r["complete"] for r in rows)
    unresolved = sum(row["status"] == "inconclusive" for row in rows)

    def candidate(record, row):
        mappings = row.get("mappings") or []
        selected = mappings[0] if mappings else None
        graph_mapping = None
        if selected:
            graph_mapping = {
                "devices": selected["device_map"],
                "nets": selected["net_map"],
                "ports": selected["port_map"],
            }
        if row["status"] in {"matched", "needs_mapping"}:
            eligibility, requirements = "eligible", (
                ["mapping_confirmation"] if row["status"] == "needs_mapping"
                else ["target_binding", "target_geometry"]
            )
        elif row["status"] == "needs_adaptation":
            eligibility, requirements = "needs_input", ["adaptation"]
        elif row["status"] == "inconclusive":
            eligibility, requirements = "inconclusive", [row.get("reason", "unknown")]
        else:
            eligibility, requirements = "rejected", []
        output = {
            "template_ref": record["template_ref"],
            "origins": list(record.get("origins", ["private"])),
            "eligibility": eligibility,
            "hard_checks": ["typed_core_embedding"] if selected else [],
            "rank_features": None,
            "mapping": graph_mapping,
            "requirements": requirements,
            "status": row["status"],
            "reason": row.get("reason"),
            "complete": row["complete"],
            "states": row["states"],
            "mappings": mappings,
            "adaptation_required": bool(row.get("adaptation_required")),
        }
        if eligibility == "eligible" and graph_mapping:
            output["rank_features"] = {
                "adaptation_cost": 0,
                "target_coverage": 1.0,
                "style_penalty": 0,
                "origin_priority": min(
                    TIERS.index(origin) for origin in output["origins"] if origin in TIERS
                ),
                "mapping_digest": digest(graph_mapping),
            }
        return output

    pool_rows = [candidate(pool[row["template_ref"]], row) for row in rows]
    eligible = [row for row in pool_rows if row["eligibility"] == "eligible"]
    needs_input = [row for row in pool_rows if row["eligibility"] == "needs_input"]
    unknown = [row for row in pool_rows if row["eligibility"] == "inconclusive"]
    rejected = [row for row in pool_rows if row["eligibility"] == "rejected"]
    eligible.sort(key=ranking_key)
    ordered = eligible + needs_input + unknown + rejected
    best = ordered[0] if ordered else None
    if clock() - started >= timeout or states > max_states:
        complete = False
        status, stop_reason = "inconclusive", "budget_exhausted"
    elif stopped_early:
        status = "needs_mapping" if best["status"] == "needs_mapping" else "needs_geometry"
        stop_reason = "first_eligible"
    elif unresolved or len(rows) != len(pool) or not complete:
        stop_reason = "unresolved_candidates" if unresolved else "budget_exhausted"
        status = "inconclusive"
    elif needs_input and len(eligible) + len(needs_input) > 1:
        status, stop_reason = "needs_selection", "exhausted"
    elif needs_input:
        status, stop_reason = "needs_adaptation", "exhausted"
    elif any(row["status"] == "needs_mapping" for row in rows):
        status, stop_reason = "needs_mapping", "exhausted"
    elif len(eligible) > 1:
        status, stop_reason = "needs_selection", "exhausted"
    elif eligible:
        status, stop_reason = "needs_geometry", "exhausted"
    else:
        status, stop_reason = "no_match", "exhausted"
    if status == "no_match":
        # A complete rejection has no selected candidate in the public
        # envelope. The rejected rows remain available through the bounded
        # compatibility `candidates` alias for diagnostics only.
        best, alternatives = None, []
    else:
        alternatives = ordered[1:1 + return_alternatives]
    issues = []
    if status == "inconclusive":
        issues.append(issue(stop_reason, "Core search did not establish a complete result"))
    elif status == "needs_selection":
        issues.append(issue("selection_required", "Select a fixed template reference"))
    elif status == "needs_mapping":
        issues.append(
            issue(
                "mapping_confirmation",
                "Core embedding requires explicit mapping confirmation",
                best["template_ref"] if best else None,
            )
        )
    elif status == "needs_adaptation":
        issues.append(
            issue(
                "adaptation_required",
                "Target contains a declared residual group requiring adaptation",
                best["template_ref"] if best else None,
                stage="adaptation",
            )
        )
    elif status == "needs_geometry":
        issues.append(
            issue(
                "needs_geometry",
                "Core embedding is valid; target geometry remains to be prepared",
                best["template_ref"] if best else None,
                stage="geometry",
            )
        )
    trace = [
        {
            "template_ref": row["template_ref"],
            "outcome": row["status"],
            "reason": row.get("reason") or None,
        }
        for row in rows[:32]
    ]
    result = _core_result(
        status, stop_reason, started=started, states=states, examined=len(rows),
        remaining=len(pool) - len(rows), snapshot_ref="offline_core_" + digest(sorted(pool)),
        best=best, alternatives=alternatives,
        candidates=[] if status == "no_match" else ordered,
        issues=issues, timeout=timeout, max_states=max_states, unresolved=unresolved,
        complete=complete, stop_reason=stop_reason, selection_mode=selection_mode, clock=clock,
    )
    result["diagnostics"]["trace"] = trace
    result["diagnostics"]["trace_omitted"] = max(0, len(rows) - len(trace))
    # Re-serialize after adding trace so the common validator covers the final
    # envelope, including its response budget.
    return serialize_result(result)
