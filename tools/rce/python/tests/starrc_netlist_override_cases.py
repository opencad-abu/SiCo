from __future__ import annotations

import sys
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.gen_starrc import generate_starrc  # noqa: E402
from starrc_netlist_customize_fixtures import _config


def test_starrc_rejects_common_options_that_override_required_geometry(
    tmp_path: Path,
) -> None:
    cfg = _config(
        tmp_path,
        netlist={"parasitic_res_layer": True},
    )
    common_opt = tmp_path / "tech/RCmax/common.opt"
    common_opt.write_text(
        "REDUCTION: NO\nREDUCTION: YES\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=r"require REDUCTION: NO.*common\.opt.*REDUCTION: YES",
    ):
        generate_starrc(cfg, cfg.context())

    assert not (tmp_path / "run/log/star.cmd").exists()



def test_starrc_rejects_common_options_that_reduce_required_via_nodes(
    tmp_path: Path,
) -> None:
    cfg = _config(
        tmp_path,
        netlist={"parasitic_coordinates": True},
    )
    (tmp_path / "tech/RCmax/common.opt").write_text(
        "KEEP_VIA_NODES: NO\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=r"require KEEP_VIA_NODES: YES.*common\.opt.*KEEP_VIA_NODES: NO",
    ):
        generate_starrc(cfg, cfg.context())

    assert not (tmp_path / "run/log/star.cmd").exists()



def test_starrc_rejects_required_setting_overridden_by_common_include(
    tmp_path: Path,
) -> None:
    cfg = _config(tmp_path, netlist={"parasitic_coordinates": True})
    included = tmp_path / "override.opt"
    included.write_text("KEEP_VIA_NODES: NO\n", encoding="utf-8")
    (tmp_path / "tech/RCmax/common.opt").write_text(
        f'INCLUDE_FILE: "{included}"\n',
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=r"require KEEP_VIA_NODES: YES.*common\.opt.*override\.opt.*NO",
    ):
        generate_starrc(cfg, cfg.context())

    assert not (tmp_path / "run/log/star.cmd").exists()



def test_starrc_rejects_cyclic_common_includes_for_required_settings(
    tmp_path: Path,
) -> None:
    cfg = _config(tmp_path, netlist={"parasitic_res_layer": True})
    common = tmp_path / "tech/RCmax/common.opt"
    included = tmp_path / "included.opt"
    common.write_text(f"INCLUDE_FILE: {included}\n", encoding="utf-8")
    included.write_text(f"INCLUDE_FILE: {common}\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"Cyclic StarRC INCLUDE_FILE chain"):
        generate_starrc(cfg, cfg.context())

    assert not (tmp_path / "run/log/star.cmd").exists()



@pytest.mark.parametrize(
    ("common_setting", "required"),
    (
        ("NETLIST_FORMAT: SPF", "NETLIST_FORMAT: SPEF"),
        ("EXTRACTION: C", "EXTRACTION: RC"),
    ),
)
def test_starrc_rejects_common_output_semantics_that_drop_requested_detail(
    tmp_path: Path, common_setting: str, required: str
) -> None:
    cfg = _config(
        tmp_path,
        output_type="spef",
        netlist={"parasitic_res_layer": True},
    )
    (tmp_path / "tech/RCmax/common.opt").write_text(
        f"{common_setting}\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=rf"require {required}.*common\.opt.*{common_setting}",
    ):
        generate_starrc(cfg, cfg.context())

    assert not (tmp_path / "run/log/star.cmd").exists()



def test_starrc_allows_equivalent_required_settings_at_end_of_common_options(
    tmp_path: Path,
) -> None:
    cfg = _config(
        tmp_path,
        netlist={"parasitic_coordinates": True},
    )
    common = "\n".join(
        (
            "REDUCTION: NO",
            "POWER_REDUCTION: NO",
            "NETLIST_FORMAT: SPF",
            "EXTRACTION: RC",
            "NETLIST_TAIL_COMMENTS: YES",
            "NETLIST_CONNECT_SECTION: YES",
            "NETLIST_NODE_SECTION: YES",
            "EXTRA_GEOMETRY_INFO: RES NODE",
            "KEEP_VIA_NODES: YES",
            "CAPACITOR_TAIL_COMMENTS: YES",
            "NETLIST_UNSCALED_COORDINATES: YES",
            "NETLIST_UNSCALED_RES_PROP: YES",
            "",
        )
    )
    (tmp_path / "tech/RCmax/common.opt").write_text(common, encoding="utf-8")

    command = generate_starrc(cfg, cfg.context()).read_text(encoding="utf-8")

    assert command.endswith(f"\n{common}")
