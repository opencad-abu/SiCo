import pytest
from test_pdk_standard_layout import process

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard.process_view import (
    SCHEMA,
    compile_process_view,
    validate_process_view,
)
from cadai.pdk_standard.tech_units import parse_skill_probe

PROBE = """
viewTypeUnits=(("maskLayout" "micron" 1000) ("schematic" "inch" 160))
dbuPerUU=1000.0
userUnit="micron"
mfgGrid=0.005
"""


def compile_one(**kwargs):
    return compile_process_view(
        process(),
        view_id="demo/1p10m",
        version="2026.10",
        pdk="demo",
        tech_lib="demo",
        **kwargs,
    )


def test_process_view_compiles_common_fields_and_is_deterministic():
    first = compile_one(engine_views={"fake": {"ready": True}})
    second = compile_one(engine_views={"fake": {"ready": True}})
    assert first == second
    assert first["schema"] == SCHEMA
    assert first["dbu_per_um"] == 1000
    assert first["grid_um"] == 0.005
    assert first["engine_views"]["fake"]["ready"] is True
    assert first["source_digest"].startswith("sha256:")


def test_process_view_rejects_non_square_grid():
    value = process()
    value["grid"]["step"] = [5e-9, 1e-8]
    with pytest.raises(PdkUnavailable, match="square"):
        compile_process_view(
            value,
            view_id="demo/1p10m",
            version="2026.10",
            pdk="demo",
            tech_lib="demo",
        )


def test_process_view_validates_its_own_digest_and_engine_views():
    view = compile_one()
    view["source_digest"] = "not-a-digest"
    with pytest.raises(PdkUnavailable, match="source_digest"):
        validate_process_view(view)
    view = compile_one()
    view["engine_views"] = []
    with pytest.raises(PdkUnavailable, match="engine_views"):
        validate_process_view(view)


def test_process_view_requires_validated_source_layout():
    value = process()
    value["unit"] = "um"
    with pytest.raises(PdkUnavailable, match="unit"):
        compile_process_view(
            value,
            view_id="demo/1p10m",
            version="2026.10",
            pdk="demo",
            tech_lib="demo",
        )


def test_process_view_uses_tech_db_units_when_supplied():
    units = parse_skill_probe(PROBE)
    view = compile_one(tech_units=units)
    assert view["stream_unit_m"] == 1e-6
    assert view["stream_precision_m"] == 1e-9
    assert view["units_source"]["source"] == "tech_db_skill"
    assert view["dbu_per_um"] == 1000


def test_process_view_rejects_tech_db_disagreement():
    units = parse_skill_probe(PROBE)
    units["database_unit"] = 2e-9
    units["stream_precision_m"] = 2e-9
    units["dbu_per_um"] = 500
    with pytest.raises(PdkUnavailable, match="database_unit"):
        compile_one(tech_units=units)
