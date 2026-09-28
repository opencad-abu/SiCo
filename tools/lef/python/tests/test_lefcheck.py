from __future__ import annotations

from pathlib import Path

import pytest

from lefpy.lefcheck import inspect_lef, validate_lef


def _write_lef(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "library.lef"
    path.write_text(body, encoding="utf-8")
    return path


def test_validate_lef_accepts_exact_macro_set(tmp_path: Path) -> None:
    path = _write_lef(
        tmp_path,
        """VERSION 5.8 ;
MACRO INVX1
  PIN A
  END A
END INVX1
MACRO NAND2X1
END NAND2X1
END LIBRARY
""",
    )

    summary = validate_lef(
        path,
        expected_version="5.8",
        expected_cells=("NAND2X1", "INVX1"),
    )

    assert summary.macros == ("INVX1", "NAND2X1")


def test_validate_lef_reports_missing_and_unexpected_macros(tmp_path: Path) -> None:
    path = _write_lef(
        tmp_path,
        "VERSION 5.8 ;\nMACRO BUFX1\nEND BUFX1\nEND LIBRARY\n",
    )

    with pytest.raises(ValueError, match=r"missing: INVX1.*unexpected: BUFX1"):
        validate_lef(path, expected_version="5.8", expected_cells=("INVX1",))


@pytest.mark.parametrize(
    ("body", "message"),
    (
        ("MACRO INVX1\nEND INVX1\nEND LIBRARY\n", "before VERSION"),
        ("VERSION 5.8 ;\nMACRO INVX1\nEND LIBRARY\n", "missing its END"),
        ("VERSION 5.8 ;\n", "missing END LIBRARY"),
        ("VERSION 5.8 ;\nEND LIBRARY\nSIZE 1 BY 1 ;\n", "after END LIBRARY"),
    ),
)
def test_inspect_lef_rejects_malformed_structure(
    tmp_path: Path, body: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        inspect_lef(_write_lef(tmp_path, body))
