"""Search dispatch and pure-result caching for template preparation."""

from __future__ import annotations

import copy

from .template_cache import search_cache_key
from .template_core_match import VERSION as EMBEDDING_VERSION
from .template_core_search import VERSION as CORE_MATCHER_VERSION
from .template_core_search import search_core
from .template_index import (
    INDEX_RULE_VERSION,
    NORMALIZER_VERSION,
)
from .template_index import (
    MATCHER_VERSION as INDEX_MATCHER_VERSION,
)
from .template_prepare_schema import VERSION as PREPARE_VERSION
from .template_prepare_schema import serialize_result
from .template_schema import RULE_VERSION, SCHEMA_V3, digest
from .template_search import MATCHER_VERSION as EXACT_MATCHER_VERSION
from .template_search import search_exact


def _record_search_digest(record):
    return digest({key: value for key, value in record.items() if key != "summary"})


def _search_dependencies(args, spec, bindings, target, options, records, origins,
                         snapshot_ref, mode, index_status, graph_map):
    return {
        "prepare": PREPARE_VERSION,
        "snapshot_ref": snapshot_ref,
        "mode": mode,
        "matcher": EXACT_MATCHER_VERSION if mode == "exact" else CORE_MATCHER_VERSION,
        "embedding": EMBEDDING_VERSION,
        "index_matcher": INDEX_MATCHER_VERSION,
        "index_normalizer": NORMALIZER_VERSION,
        "rule_version": RULE_VERSION,
        "index_rule": INDEX_RULE_VERSION,
        "ranking": options["ranking_policy"],
        "index_status": index_status,
        "options": options,
        "template_ref": args.get("template_ref"),
        "mapping": graph_map,
        "allowed_rule_refs": args.get("allowed_rule_refs", []),
        "spec": spec,
        "bindings": bindings,
        "target": target,
        "records": [
            {
                "template_ref": reference,
                "record_digest": _record_search_digest(record),
                "origins": origins.get(reference, []),
            }
            for reference, record in sorted(records.items())
        ],
    }


def _cached_search_result(cached, *, snapshot_ref, index_status):
    result = copy.deepcopy(cached)
    result["snapshot_ref"] = snapshot_ref
    result["diagnostics"]["cache"] = "hit"
    result["diagnostics"]["index"] = index_status
    result["diagnostics"]["elapsed_ms"] = 0.0
    result["diagnostics"]["budget_overrun_ms"] = 0.0
    return serialize_result(result)


def _cacheable_search(result):
    return (
        result.get("search", {}).get("complete") is True
        and result.get("status") not in {
            "inconclusive", "needs_adaptation", "invalid_request", "unavailable"
        }
    )


def search_snapshot(args, plan, target, options, records, origins, *, snapshot_ref,
                    index_status, cache):
    """Run or reuse only the search stage; later preparation always runs live."""

    spec, bindings = plan["spec"], plan["bindings"]
    mode = options["match_mode"]
    selected_ref = args.get("template_ref")
    graph_map = args.get("mapping") or {}
    port_map = graph_map.get("ports", {}) if "devices" in graph_map else graph_map.get(
        "port_map", {}
    )
    if mode == "core":
        search_records = {}
        for reference, record in records.items():
            if record.get("schema_version") != SCHEMA_V3:
                continue
            item = copy.deepcopy(record)
            item["origins"] = (
                sorted({row["tier"] for row in origins.get(reference, [])}) or ["private"]
            )
            search_records[reference] = item
    else:
        search_records = {
            reference: records[reference]
            for reference, record in records.items()
            if record.get("schema_version") != SCHEMA_V3
            and record.get("topology")
        }

    search_key = None
    cache_status = "disabled"
    cached = None
    if cache is not None and cache.enabled and snapshot_ref is not None:
        search_key = search_cache_key(
            _search_dependencies(
                args, spec, bindings, target, options, search_records, origins,
                snapshot_ref, mode, index_status, graph_map,
            )
        )
        cached = cache.get("search", search_key)
        cache_status = cache.event("search")

    if cached is not None:
        result = _cached_search_result(
            cached, snapshot_ref=snapshot_ref, index_status=index_status
        )
    elif mode == "core":
        result = search_core(
            target,
            list(search_records.values()),
            timeout=options["max_query_time_ms"] / 1000,
            max_states=options["max_match_states"],
            selection_mode=options["selection_mode"],
            return_alternatives=options["return_alternatives"],
            port_map=port_map,
            device_map=graph_map.get("device_map"),
            terminal_map=graph_map.get("terminal_map"),
            net_map=graph_map.get("net_map"),
            omitted_groups=graph_map.get("omitted_groups", []),
        )
    else:
        exact_options = {
            key: value for key, value in options.items()
            if key in {
                "selection_mode", "allowed_tiers", "return_alternatives",
                "max_query_time_ms", "max_match_states", "ranking_policy",
            }
        }
        exact_pool = [
            {
                "template_ref": reference,
                "topology": record.get("topology"),
                "origins": sorted({row["tier"] for row in origins.get(reference, [])})
                or ["private"],
            }
            for reference, record in search_records.items()
        ]
        result = search_exact(
            target,
            exact_pool,
            exact_options,
            template_ref=selected_ref,
            port_maps={selected_ref: port_map} if selected_ref else None,
        )

    if snapshot_ref is not None:
        result["snapshot_ref"] = snapshot_ref
        result["diagnostics"]["index"] = index_status
        if cached is None:
            if cache_status == "disabled":
                result["diagnostics"]["cache"] = "disabled"
            elif _cacheable_search(result) and search_key is not None:
                stored = copy.deepcopy(result)
                stored["diagnostics"]["cache"] = "miss"
                cache.put(
                    "search", search_key, stored,
                    negative=result.get("status") == "no_match",
                )
                result["diagnostics"]["cache"] = cache.event("search")
            else:
                result["diagnostics"]["cache"] = cache_status
        return serialize_result(result)
    return result
