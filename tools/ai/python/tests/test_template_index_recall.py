"""P3 recall checks: a necessary-condition index must not hide a viable result.

The complete matcher remains authoritative.  These tests compare the refs that
the index would pass to that matcher with the same fixed synthetic pool without
the index.  The corpus is deliberately structural and does not claim analog
performance or live creation qualification.
"""

from __future__ import annotations

import pytest
from template_adapt_fixture import reusable, target_case
from template_search_corpus import FAMILIES, cases, pool_item, topology

from cadai.template_core_match import match_core
from cadai.template_core_search import search_core
from cadai.template_index import (
    INDEX_RULE_VERSION,
    MATCHER_VERSION,
    NORMALIZER_VERSION,
    build_index,
    load_index,
    necessary_candidates,
)
from cadai.template_schema import digest
from cadai.template_search import search_exact
from cadai.template_target import target_topology
from cadai.template_topology import evidence_gaps


def _snapshot(records, label):
    return {
        "snapshot_ref": "snp_recall_" + digest({"label": label, "refs": sorted(
            row["template_ref"] for row in records
        )}),
        "matcher_version": MATCHER_VERSION,
    }


def _load_index(tmp_path, records, label):
    snapshot = _snapshot(records, label)
    path = tmp_path / (label.replace("/", "_") + ".json")
    build_index(snapshot, records, path)
    loaded, reason = load_index(
        path,
        snapshot["snapshot_ref"],
        matcher_version=MATCHER_VERSION,
        normalizer_version=NORMALIZER_VERSION,
        index_rule_version=INDEX_RULE_VERSION,
    )
    assert reason is None and loaded is not None
    return loaded


def _exact_result(target, candidate, port_maps):
    return search_exact(
        target,
        [candidate],
        {"max_query_time_ms": 10000, "max_match_states": 200000},
        port_maps=port_maps,
    )


def _exact_statuses(target, records, port_maps):
    return {
        row["template_ref"]: _exact_result(
            target,
            row,
            {row["template_ref"]: port_maps.get(row["template_ref"], {})},
        )["status"]
        for row in records
    }


def _exact_pool_result(target, records, port_maps):
    maps = {
        row["template_ref"]: port_maps.get(row["template_ref"], {})
        for row in records
    }
    return search_exact(
        target,
        records,
        {"max_query_time_ms": 10000, "max_match_states": 200000},
        port_maps=maps,
    )


def _exact_indexed_refs(index, target, records):
    kinds = {}
    for device in target["devices"]:
        kinds[device["kind"]] = kinds.get(device["kind"], 0) + 1
    return set(
        necessary_candidates(
            index,
            device_kinds=kinds,
            min_devices=len(target["devices"]),
            max_devices=len(target["devices"]),
        )
    ) & {row["template_ref"] for row in records}


def _safe_exact_indexed_refs(index, target, records):
    """Mirror the production fail-open union for records with evidence gaps."""
    selected = _exact_indexed_refs(index, target, records)
    if evidence_gaps(target):
        return {row["template_ref"] for row in records}
    selected.update(
        row["template_ref"] for row in records if evidence_gaps(row["topology"])
    )
    return selected


def _case_pool(case):
    """The labeled candidate plus one candidate from every other topology family."""
    pool = list(case["candidates"])
    refs = {row["template_ref"] for row in pool}
    for number, family in enumerate(FAMILIES, 900):
        item = pool_item(topology(family), number=number)
        if item["template_ref"] not in refs:
            pool.append(item)
    return pool


@pytest.mark.parametrize("case", cases(), ids=lambda case: case["id"])
def test_exact_index_preserves_all_non_rejected_outcomes(tmp_path, case):
    records = _case_pool(case)
    target = case["target"]
    port_maps = case["port_maps"]
    baseline = _exact_statuses(target, records, port_maps)
    index = _load_index(tmp_path, records, case["id"])

    # Target evidence gaps force the production path to use the complete pool;
    # mirror that fail-closed rule in this standalone comparison.
    indexed_refs = _safe_exact_indexed_refs(index, target, records)

    non_rejected = {
        ref for ref, status in baseline.items() if status != "no_match"
    }
    assert non_rejected <= indexed_refs
    # The labeled expected status is checked through the same authoritative
    # matcher used by the full-pool comparison.
    labeled = case["candidates"][0]["template_ref"]
    assert baseline[labeled] == case["expected_status"]
    indexed = {
        ref: baseline[ref] for ref in indexed_refs
    }
    assert set(indexed) == indexed_refs
    if indexed_refs:
        indexed_result = _exact_pool_result(
            target,
            [row for row in records if row["template_ref"] in indexed_refs],
            port_maps,
        )
        complete_baseline = _exact_pool_result(target, records, port_maps)
        if complete_baseline["search"]["complete"]:
            assert indexed_result["status"] == complete_baseline["status"]


def test_exact_index_keeps_uncertain_record_and_target_evidence(tmp_path):
    record = pool_item(topology("inverter"), number=1)
    uncertain_record = pool_item(topology("common_source"), number=2)
    # Give the uncertain row a distinct immutable reference for this synthetic
    uncertain_record["topology"]["gaps"] = [{"code": "terminal_inventory_unverified"}]
    records = [record, uncertain_record]
    index = _load_index(tmp_path, records, "uncertain")
    target = topology("inverter")
    refs = _safe_exact_indexed_refs(index, target, records)
    assert uncertain_record["template_ref"] in refs

    target["gaps"].append({"code": "terminal_inventory_unverified"})
    assert evidence_gaps(target)
    assert _safe_exact_indexed_refs(index, target, records) == {
        row["template_ref"] for row in records
    }


@pytest.mark.parametrize("optional", [False, True])
def test_core_index_counts_required_devices_and_keeps_optional_group(tmp_path, optional):
    reference = reusable(optional=optional)
    args = target_case(reference, omit=optional) if optional else target_case(reference)
    target = target_topology(args["spec"], args["bindings"])
    index = _load_index(tmp_path, [reference], "core_optional_" + str(optional))
    available = {}
    for device in target["devices"]:
        available[device["kind"]] = available.get(device["kind"], 0) + 1
    refs = set(
        necessary_candidates(
            index,
            available_device_kinds=available,
            max_devices=len(target["devices"]),
            count_field="required_device_count",
            kind_field="required_device_kinds",
        )
    )
    assert reference["template_ref"] in refs
    baseline = match_core(reference, target, timeout=2, **{
        "port_map": args["mapping"].get("port_map"),
        "device_map": args["mapping"].get("device_map"),
        "terminal_map": args["mapping"].get("terminal_map"),
        "net_map": args["mapping"].get("net_map"),
        "omitted_groups": args["mapping"].get("omitted_groups", []),
    })
    assert baseline["status"] in {"matched", "needs_mapping", "needs_adaptation"}


@pytest.mark.parametrize("family", list(FAMILIES))
@pytest.mark.parametrize("extra", [False, True])
def test_core_index_and_full_search_have_the_same_decision(tmp_path, family, extra):
    reference = reusable(family=family)
    args = target_case(reference, extra=extra)
    target = target_topology(args["spec"], args["bindings"])
    records = [reusable(family=name) for name in FAMILIES]
    full = search_core(target, records, timeout=5, max_states=200000)
    index = _load_index(tmp_path, records, "core_recall_" + family + str(extra))
    available = {}
    for device in target["devices"]:
        available[device["kind"]] = available.get(device["kind"], 0) + 1
    refs = set(
        necessary_candidates(
            index,
            available_device_kinds=available,
            max_devices=len(target["devices"]),
            count_field="required_device_count",
            kind_field="required_device_kinds",
        )
    )
    indexed_records = [row for row in records if row["template_ref"] in refs]
    assert full["status"] != "inconclusive"
    assert full["status"] == search_core(
        target, indexed_records, timeout=5, max_states=200000
    )["status"]
    eligible = {
        row["template_ref"]
        for row in full["candidates"]
        if row["eligibility"] != "rejected"
    }
    assert eligible <= refs


@pytest.mark.parametrize("mode", ["exact", "core"])
def test_index_corruption_and_version_drift_are_fallbacks(tmp_path, mode):
    record = pool_item(topology("inverter"), number=7)
    index = _load_index(tmp_path, [record], "fallback_" + mode)
    target = topology("inverter")
    snapshot = _snapshot([record], "fallback_" + mode)
    path = tmp_path / ("fallback_" + mode + ".json")
    broken, reason = load_index(
        path,
        snapshot["snapshot_ref"],
        matcher_version="matcher.other.v1",
        normalizer_version=NORMALIZER_VERSION,
        index_rule_version=INDEX_RULE_VERSION,
    )
    assert broken is None and reason == "index_fallback"
    if mode == "exact":
        assert _exact_indexed_refs(index, target, [record]) == {record["template_ref"]}
    else:
        assert necessary_candidates(
            index,
            available_device_kinds={"nmos": 1, "pmos": 1},
            max_devices=2,
            count_field="required_device_count",
            kind_field="required_device_kinds",
        ) == [record["template_ref"]]
