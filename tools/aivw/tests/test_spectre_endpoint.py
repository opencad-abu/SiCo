import pytest

from aivworkbench.spectre_data import measure_csv
from aivworkbench.workspace import sha256_file


def test_serialized_endpoint_roundoff_is_not_missing_coverage(tmp_path):
    path = tmp_path/"wave.csv"
    path.write_text("time[s],v[V]\n0,0\n0.0004,1\n0.00079999999999999364,2\n")
    before = sha256_file(path)
    result = measure_csv(path, {"v": "V"}, [.0006, .0008], .0008)
    assert result["samples"][-1]["values"]["v"] == 2.
    assert result["samples"][0]["values"]["v"] == pytest.approx(1.5)
    assert sha256_file(path) == before


def test_actual_missing_tail_and_extrapolation_still_rejected(tmp_path):
    path = tmp_path/"wave.csv"
    path.write_text("time[s],v[V]\n0,0\n0.000799,2\n")
    with pytest.raises(ValueError, match="full experiment"):
        measure_csv(path, {"v": "V"}, [.0008], .0008)
    path.write_text("time[s],v[V]\n0,0\n0.00079999999999999364,2\n")
    with pytest.raises(ValueError, match="extrapolation forbidden"):
        measure_csv(path, {"v": "V"}, [.00080000001], .0008)
