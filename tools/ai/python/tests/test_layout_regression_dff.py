import json
from pathlib import Path


def test_dff_regression_case_is_bounded_and_references_pdk_header():
    path = Path(__file__).parents[2] / "docs/examples/layout-regression-dff.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert value["schema"] == "sico.layout.regression_case.v1"
    assert (value["library"], value["cell"]) == ("SicoTest", "DFF")
    assert value["views"]["layout"] == "layout"
    assert value["expected"]["terminals"] == ["D", "CLK", "Q", "VDD", "VSS"]
    assert value["expected"]["layout_instances"] == 13
    assert value["expected"]["lvs"]["result"] == "CORRECT"
    assert value["lvs_inputs"]["cdl_header_file"].endswith("/empty_subckt.sp")
    assert set(value["expected"]["drc"]["result_rules"]) == {"BD_1", "BD_2a"}
