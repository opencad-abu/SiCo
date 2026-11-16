"""Conflict summaries are deterministic and pair-level, never whole-box guesses."""

from cadai.template_route_conflicts import conflict_groups, select_conflict


def wire(net, points):
    return dict(net=net, points=points)


def test_conflict_groups_count_segment_hits_and_stable_witness():
    routes = {
        "B": [wire("B", [[0, -1], [0, 1]]), wire("B", [[3, 0], [4, 0]])],
        "A": [wire("A", [[-1, 0], [1, 0]])],
        "C": [wire("C", [[10, 10], [11, 10]])],
    }
    rows = conflict_groups(routes, 0)
    assert rows == [{"nets": ("A", "B"), "count": 1,
                    "segments": (((-1, 0), (1, 0)), ((0, -1), (0, 1))),
                    "witnesses": [(((-1, 0), (1, 0)), ((0, -1), (0, 1)))],
                    "truncated": False}]


def test_selection_uses_visit_count_then_hit_count_then_geometry():
    rows = [dict(nets=("A", "B"), count=2, segments=([[0, 0], [2, 0]], [[1, -1], [1, 1]])),
            dict(nets=("A", "C"), count=1, segments=([[0, 2], [2, 2]], [[1, 1], [1, 3]]))]
    assert select_conflict(rows, {})["nets"] == ("A", "C")
    assert select_conflict(rows, {("A", "C"): 1})["nets"] == ("A", "B")
