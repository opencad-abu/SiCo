from __future__ import annotations

import json
from pathlib import Path
import sys

REFERENCE = Path(__file__).resolve().parents[2] / "reference" / "cadence-skill"
sys.path.insert(0, str(REFERENCE))

from pdf_facts import parse_text  # noqa: E402


def test_pas_gte_pdf_facts_are_text_only_and_mark_aliases() -> None:
    pages = [""] * 229
    pages[162] = "SKILL Functions\nPasGetLibProps\nPasGetPdkParams\n"
    pages[163] = "Cadence PAS GTE PCell SKILL Utility Functions\n"
    pages[164] = "September 2007 Product Version 03.01.09\n"
    pages[165] = (
        "PasGetLibProps\n"
        "PasGetLibProps( S_libName [g_force] ) => a_devicePropertyTable\n"
        "Description\nRetrieves the library CDF device property table.\n"
        "Arguments\nS_libName\n     Library name.\n"
        "Value Returned\na_devicePropertyTable\n     Property table.\n"
        "Example\nPasGetLibProps( \"myLib\" )\n"
        "Reference\nNone.\n"
    )
    pages[226] = "PasGetPdkParams\n     See PasGetCellProps.\n"
    records = parse_text("\f".join(pages))

    assert [record["name"] for record in records] == [
        "PasGetLibProps",
        "PasGetPdkParams",
    ]
    assert records[0]["source_kind"] == "pdf"
    assert records[0]["product_version"] == "03.01.09"
    assert json.loads(records[0]["arguments_json"]) == [
        {"name": "S_libName", "description": "Library name."}
    ]
    assert records[1]["kind"] == "alias"
    assert records[1]["description"] == "See PasGetCellProps."
    assert "<" not in "".join(record["body"] for record in records)
