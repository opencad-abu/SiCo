"""The common coordinator commits or discards the whole cross-net change."""

import copy

import pytest
from test_template_route_network_search import setup

from cadai import template_route_networks as networks
from cadai.template_route_coordinate import coordinate_routes
from cadai.template_route_paths import path_gaps


def context():
    original, members, layout, placed, pins, _ = setup()
    return (dict(A=original["A"], B=members["B"]),
            [dict(net="A", status="reused", reasons=[], transform={}),
             dict(net="B", status="stub", reasons=["collision"])],
            members, {}, dict(B=original["B"]), layout, placed, pins)


def test_public_coordination_falls_through_to_joint_search():
    args = context()
    before = copy.deepcopy(args)
    coordinate_routes(*args)
    assert all(r["method"] == "coordinated_network_search" for r in args[1])
    assert all(r["status"] == "rerouted" and not r["reasons"] for r in args[1])
    assert all(args[0][n] != before[0][n] for n in ("A", "B"))
    assert args[2:] == before[2:]
    for n, ws in args[0].items():
        assert not path_gaps(ws, args[2][n], *args[5:], sum(args[0].values(), []))


@pytest.mark.parametrize("failure", ["search", "late_check", "outside_check", "port",
                                     "unverified_neighbor", "budget", "group_budget"])
def test_atomic_rollback_including_previously_successful_nets(monkeypatch, failure):
    args = context()
    if failure == "search":
        monkeypatch.setattr(networks, "network_paths", lambda *a: None)
    elif failure == "late_check":
        monkeypatch.setattr(networks, "path_gaps", lambda *a: ["failure"])
    elif failure == "outside_check":
        args[0]["C"] = [dict(net="C", points=[[20, 0], [21, 0]])]
        args[2]["C"] = [dict(net="C", endpoint=dict(port="C"), points=[[20, 0], [21, 0]])]
        args[1].append(dict(net="C", status="reused", reasons=[]))
        real = networks.path_gaps
        monkeypatch.setattr(networks, "path_gaps", lambda ws, *a:
                            ["failure"] if ws[0]["net"] == "C" else real(ws, *a))
    elif failure == "port":
        args[3]["external"] = [dict(net="external", points=[[-1, 0], [-.5, 0]])]
    elif failure == "unverified_neighbor":
        args[1][0]["status"] = "stub"
    elif failure == "budget":
        monkeypatch.setattr(networks, "MAX_SEARCHES", 0)
    else:
        monkeypatch.setattr(networks, "MAX_NETS", 1)
    before = copy.deepcopy(args)
    networks.recover_networks(*args)
    assert args[0] == before[0]
    assert [{k: v for k, v in r.items() if k != "diagnostics"} for r in args[1]] == before[1]
    assert args[2:] == before[2:]
    assert args[1][1]["diagnostics"][-1]["code"] == "target_network_search_exhausted"


def test_fallback_stubs_are_never_promoted_without_pending_evidence():
    args = context()
    args[4].clear()
    before = copy.deepcopy(args)
    coordinate_routes(*args)
    assert args == before


@pytest.mark.parametrize("success", [True, False])
def test_latest_search_outcome_survives_diagnostic_cap(monkeypatch, success):
    args = context()
    for row in args[1]:
        row["diagnostics"] = [dict(code="prior_attempt", index=i) for i in range(32)]
    if not success:
        monkeypatch.setattr(networks, "MAX_SEARCHES", 0)
    networks.recover_networks(*args)
    assert len(args[1][1]["diagnostics"]) == 32
    assert args[1][1]["diagnostics"][-1]["code"] == (
        "target_network_search_applied" if success else "target_network_search_exhausted")
