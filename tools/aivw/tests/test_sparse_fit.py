import math

import pytest

from aivworkbench.plugins.sampled_state import _solve, features
from aivworkbench.plugins.sparse_fit import solve_positive_system


def test_preconditioned_fit_matches_pivoted_solution():
    matrix = [[4., 1., .2], [1., 3., .4], [.2, .4, 2.]]
    rhs = [1., -2., 5.]
    assert solve_positive_system(matrix, rhs) == pytest.approx(_solve(matrix, rhs), rel=1e-9)


@pytest.mark.parametrize("matrix,rhs", [([[0.]], [1.]), ([[1.]], [math.nan]), ([[1., 2.]], [1.])])
def test_solver_rejects_invalid_evidence(matrix, rhs):
    with pytest.raises(ValueError):
        solve_positive_system(matrix, rhs)


def test_signed_nonlinear_slew_features_preserve_direction_and_no_time_axis():
    model = {"axes": [{"knots": [0., .5, 1.], "slew_scale_per_s": 1.}],
             "identification_options": {"slew_features": "signed_polynomial"}}
    positive = features(model, [.5], [.25], 1.)
    negative = features(model, [.5], [.75], 1.)
    assert positive[:3] == negative[:3]
    assert positive[3:12] == negative[12:21]
    assert positive[12:] == [0.]*9
    assert negative[3:12] == [0.]*9
    assert features(model, [.5], [.5], 1.)[3:] == [0.]*18
