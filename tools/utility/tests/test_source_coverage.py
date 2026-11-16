"""Coverage summary arithmetic stays independent from a real coverage run."""

from utility.source_coverage import totals


def test_totals_aggregate_lines_and_branches():
    files = {
        "a.py": {"summary": {"num_statements": 10, "covered_lines": 8,
                             "num_branches": 4, "covered_branches": 2}},
        "b.py": {"summary": {"num_statements": 5, "covered_lines": 5,
                             "num_branches": 2, "covered_branches": 2}},
    }
    result = totals(files)
    assert result["statements"] == 15
    assert result["covered_lines"] == 13
    assert result["branches"] == 6
    assert result["covered_branches"] == 4
    assert round(result["line_pct"], 2) == 86.67
    assert round(result["branch_pct"], 2) == 66.67


def test_totals_empty_is_complete():
    result = totals({})
    assert result["line_pct"] == 100.0
    assert result["branch_pct"] == 100.0
