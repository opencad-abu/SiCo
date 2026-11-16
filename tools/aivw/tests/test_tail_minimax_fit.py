import pytest
from aivworkbench.plugins.tail_minimax_fit import solve


def test_exact_linear_current_response():
    rows=[([1.,x],.0002+.0001*x,.0005+.0001*x) for x in [-2.,-1.,0.,1.,2.]]
    coeff,report=solve(rows)
    assert coeff==pytest.approx([.0002,.0001],abs=1e-10)
    assert report['acceptance_policy_used'] is False


def test_bounded_reweighting_retains_best_iterate():
    rows=[([1.],0.,.001)]*20+[([1.],.0002,.001)]
    coeff,report=solve(rows,passes=6)
    assert report['best_maximum_relative_residual']==min(report['maximum_relative_residual_by_pass'])
    assert report['best_maximum_relative_residual']<report['maximum_relative_residual_by_pass'][0]
    assert 0.<coeff[0]<.0002
    with pytest.raises(ValueError):solve(rows,passes=9)
    with pytest.raises(ValueError):solve([([float('nan')],0.,1.)])
