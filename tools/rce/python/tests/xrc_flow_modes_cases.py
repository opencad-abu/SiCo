from __future__ import annotations

import sys
from pathlib import Path

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.generators import generate_all  # noqa: E402
from rcepy.runner import RceRunner  # noqa: E402
from xrc_test_support import (  # noqa: E402
    install_fake_calibre,
    make_xrc_config,
    xrc_stages,
)


@pytest.mark.parametrize(
    ("rc_type", "pdb_flag", "fmt_flag"),
    [
        ("RCC", "-rcc", "-all"),
        ("RC", "-rc", "-all"),
        ("R", "-r", "-r"),
        ("C", "-c", "-c"),
    ],
)
def test_xrc_rc_mode_selects_pdb_and_formatter_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rc_type: str,
    pdb_flag: str,
    fmt_flag: str,
) -> None:
    cfg, _ = make_xrc_config(tmp_path, rc_type=rc_type)
    calibre = install_fake_calibre(tmp_path / "mgc")
    monkeypatch.setenv("MGC_HOME", str(calibre.parents[1]))

    stages = {stage.name: stage for stage in xrc_stages(cfg)}

    assert stages["xrc_pdb"].command[2:4] == ["-pdb", pdb_flag]
    assert stages["xrc_fmt"].command[2:4] == ["-fmt", fmt_flag]



@pytest.mark.parametrize("rc_type", ["NONE", "No-RC", "noRC"])
def test_xrc_no_rc_uses_simple_formatter_without_pdb(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rc_type: str,
) -> None:
    cfg, paths = make_xrc_config(tmp_path, rc_type=rc_type)
    calibre = install_fake_calibre(tmp_path / "mgc")
    trace = tmp_path / "calibre.trace"
    monkeypatch.setenv("MGC_HOME", str(calibre.parents[1]))
    monkeypatch.setenv("FAKE_CALIBRE_TRACE", str(trace))

    generate_all(cfg, cfg.context())
    runset = paths["runset"].read_text(encoding="utf-8")
    stages = xrc_stages(cfg)

    assert f'PEX NETLIST SIMPLE "{paths["output"]}" DSPF SOURCENAMES' in runset
    assert "PEX EXTRACT TEMPERATURE" not in runset
    assert [stage.name for stage in stages] == ["xrc_lvs", "xrc_fmt"]
    assert stages[-1].command == [
        str(calibre),
        "-xrc",
        "-fmt",
        "-simple",
        "-nowait",
        "_xrc.cal_",
    ]

    assert RceRunner(cfg).run() == 0
    commands = trace.read_text(encoding="utf-8").splitlines()
    assert len(commands) == 2
    assert not any("-pdb" in command for command in commands)
    assert not (paths["svdb"] / "pex.db").exists()
    assert paths["output"].is_file()



@pytest.mark.parametrize(
    ("output_type", "svrf_format"),
    [
        ("view", "CALIBREVIEW"),
        ("dspf", "DSPF"),
        ("sp", "HSPICE"),
        ("spice", "HSPICE"),
    ],
)
def test_xrc_no_rc_maps_the_three_rce_output_formats(
    tmp_path: Path, output_type: str, svrf_format: str
) -> None:
    cfg, paths = make_xrc_config(
        tmp_path, rc_type="NONE", output_type=output_type
    )
    if output_type == "view":
        cfg = cfg.replace('extract', 'view', value={
            "kind": "calibre",
            "library": "no_rc_test",
            "cell": "top",
            "name": "calibre",
        })
        (paths["corner_dir"] / "calview.cellmap").write_text(
            "((p cap c) (analogLib cap symbol) ((PLUS) (MINUS)))\n",
            encoding="utf-8",
        )
    ctx = cfg.context()

    generate_all(cfg, ctx)

    runset = paths["runset"].read_text(encoding="utf-8")
    assert (
        f'PEX NETLIST SIMPLE "{cfg.output_path(ctx)}" '
        f"{svrf_format} SOURCENAMES"
    ) in runset



@pytest.mark.parametrize("output_type", ["hspice", "spef", "spectre"])
def test_xrc_no_rc_rejects_formats_outside_the_rce_contract(
    tmp_path: Path, output_type: str
) -> None:
    cfg, _ = make_xrc_config(
        tmp_path, rc_type="NONE", output_type=output_type
    )

    with pytest.raises(
        ValueError, match="NONE/noRC output supports only.*view, dspf, and spice"
    ):
        generate_all(cfg, cfg.context())



@pytest.mark.parametrize(
    "configure",
    [
        lambda cfg: cfg.replace("netlist", value={"parasitic_coordinates": True}),
        lambda cfg: cfg.replace("filter", value={"cap_value": "0.01"}),
        lambda cfg: cfg.replace("selection", value={"net_enable": True, "nets": ["VDD"]}),
        lambda cfg: cfg.replace("netlist", value={"dspf_remove_instances": "TRUE"}),
    ],
)
def test_xrc_no_rc_rejects_parasitic_only_options(
    tmp_path: Path, configure
) -> None:
    cfg, _ = make_xrc_config(tmp_path, rc_type="NONE")
    cfg = configure(cfg)

    with pytest.raises(ValueError, match="Calibre XRC NONE/noRC"):
        generate_all(cfg, cfg.context())
