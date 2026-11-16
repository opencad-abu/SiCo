"""Native wire normalization must preserve the exact covered grid edges."""

import pytest
from template_pdk_support import _edge_set

from cadai.circuit_wire_payload import draw_points


@pytest.mark.parametrize("points,expected", [
    ([[0, -2.125], [0, -2.875], [0, -3.375]], [[0, -2.125], [0, -3.375]]),
    ([[0, 0], [1, 0], [1, 0], [2, 0], [2, 1]], [[0, 0], [2, 0], [2, 1]]),
    ([[0, 0], [2, 0], [1, 0]], [[0, 0], [2, 0], [1, 0]]),
])
def test_native_points_preserve_grid_edges_corners_and_reversals(points, expected):
    result = draw_points(points)
    assert result == expected
    assert _edge_set([["N", result]]) == _edge_set([["N", points]])
    assert result[0] == points[0] and result[-1] == points[-1]
