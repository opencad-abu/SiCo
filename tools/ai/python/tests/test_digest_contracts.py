from __future__ import annotations

import pytest

from cadai.template_schema import canonical, digest


def test_template_digest_vector_is_utf8_and_order_independent() -> None:
    value = {"中文": "值", "z": 1.5, "a": [True, None]}
    expected = "c1c65a48fc50e698e10ec7a7dc1bc2a6c9b7d17f0dce8c3c9b37afb77a3fef82"
    assert digest(value) == expected
    assert digest({"a": [True, None], "z": 1.5, "中文": "值"}) == expected
    assert "中文" in canonical(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_template_digest_rejects_nonfinite_values(value: float) -> None:
    with pytest.raises(ValueError, match="not JSON compliant"):
        digest(value)
