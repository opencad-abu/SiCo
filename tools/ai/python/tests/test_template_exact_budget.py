"""Typed candidate pruning preserves VF2 mappings without consuming impossible states."""

import copy

import networkx as nx
import pytest
from template_search_corpus import FAMILIES, cases, pool_item, topology

from cadai import template_graph, template_search
from cadai.template_schema import canonical


@pytest.mark.parametrize("family", list(FAMILIES))
def test_identity_fits_typed_search_budget(family):
    source = topology(family)
    nodes = len(template_graph.graph_data(source)[0])
    result = template_graph.match(source, source, max_states=2 * nodes)
    assert result["status"] == "matched", result
    assert nodes <= result["states"] <= 2 * nodes


def test_target_evidence_is_validated_once_per_search(monkeypatch):
    target = topology("inverter")
    calls = []
    original = template_search.evidence_gaps

    def checked(value):
        calls.append(value.get("request_target", False))
        return original(value)

    monkeypatch.setattr(template_search, "evidence_gaps", checked)
    target["request_target"] = True
    result = template_search.search_exact(target, [pool_item(topology(f), i)
                                                  for i, f in enumerate(FAMILIES)])
    assert result["search"]["complete"]
    assert calls.count(True) == 1
    assert calls.count(False) == len(FAMILIES)


def baseline_mapping(left, right, port_map):
    graphs = []
    for top, rename in ((left, port_map), (right, {})):
        labels, edges = template_graph.graph_data(top, rename)
        graph = nx.Graph()
        graph.add_nodes_from((n, {"label": canonical(label)}) for n, label in labels.items())
        graph.add_edges_from(edges)
        graphs.append(graph)
    matcher = nx.algorithms.isomorphism.GraphMatcher(
        *graphs, node_match=lambda a, b: a["label"] == b["label"])
    try:
        return next(matcher.isomorphisms_iter(), None)
    finally:
        matcher.reset_recursion_limit()


@pytest.mark.parametrize("case", cases(), ids=lambda c: c["id"])
def test_pruning_keeps_unpruned_first_mapping(case):
    source = case["candidates"][0]
    left, right = source["topology"], case["target"]
    ports = case["port_maps"].get(source["template_ref"], {})
    expected = baseline_mapping(left, right, ports)
    result = template_graph.match(left, right, ports)
    if result["status"] == "inconclusive":
        assert "bus" in result["reason"]
    elif expected is None:
        assert result["status"] == "different"
    else:
        assert result["status"] == "matched"
        assert result["mapping"]["devices"] == {
            d["id"]: expected["d:" + d["id"]][2:] for d in left["devices"]}
        assert result["mapping"]["nets"] == {
            n["id"]: expected["n:" + n["id"]][2:] for n in left["nets"]}


def test_equal_labels_and_degrees_are_not_a_connectivity_proof():
    source = topology("inverter")
    target = copy.deepcopy(source)
    # Swap two ports: degree/label counts still agree, connectivity does not.
    target["ports"][0]["net"], target["ports"][1]["net"] = (
        target["ports"][1]["net"], target["ports"][0]["net"])
    assert baseline_mapping(source, target, {}) is None
    assert template_graph.match(source, target)["status"] == "different"


def test_tiny_state_budget_still_preserves_unknown():
    source = topology("inverter")
    result = template_graph.match(source, source, max_states=1)
    assert result["status"] == "inconclusive"
    assert result["reason"] == "search_budget_exceeded"


def test_rejected_candidate_pairs_still_obey_deadline(monkeypatch):
    from types import SimpleNamespace

    source = topology("inverter")
    target = copy.deepcopy(source)
    for device in target["devices"]:
        device["kind"] = "different_kind"
    ticks = iter(range(1000))
    monkeypatch.setattr(template_graph, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    result = template_graph.match(source, target, timeout=0.5)
    assert result["status"] == "inconclusive"
    assert result["states"] == 0
