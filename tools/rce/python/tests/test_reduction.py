from __future__ import annotations

from pathlib import Path
import sys

import pytest


PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.config import RceConfig  # noqa: E402
from rcepy.reduction import (  # noqa: E402
    build_reduction_jobs,
    reduced_file_path,
    reduced_output_paths,
    reduced_view_name,
)


def _config(
    tmp_path: Path,
    *,
    output_type: str = "dspf",
    reduction: dict[str, object] | None = None,
    corners: list[str] | None = None,
    view_kind: str = "smart",
) -> RceConfig:
    tmp_path.mkdir(parents=True, exist_ok=True)
    run_dir = tmp_path / "run"
    cds_lib = tmp_path / "cds.lib"
    cds_lib.write_text("DEFINE layout_lib ./layout_lib\n", encoding="utf-8")
    return RceConfig(
        raw={
            "run": {"run_dir": str(run_dir), "cds_lib": str(cds_lib)},
            "input": {
                "type": "OA",
                "schematic": {
                    "lib": "source_lib",
                    "cell": "source_top",
                    "view": "schematic",
                },
                "layout": {
                    "lib": "layout_lib",
                    "cell": "layout_top",
                    "view": "layout",
                },
            },
            "lvs": {"tool": "Calibre"},
            "extract": {
                "tool": "QRC",
                "tech_dir": str(tmp_path / "tech"),
                "corner": (corners or ["RCmax"])[0],
                "corners": corners or ["RCmax"],
                "temperature": "25",
                "rc_type": "R+Cg+Cc",
                "output_type": output_type,
                "name_source": "layout",
                "view": {
                    "kind": view_kind,
                    "library": "layout_lib",
                    "cell": "layout_top",
                    "name": "av_extracted",
                },
            },
            "runtime": {"ext_cpus": "4"},
            "netlist": {"output_path": str(run_dir / f"top.{output_type}")},
            "reduction": {"enabled": True, **(reduction or {})},
        },
        config_path=tmp_path / "rce.toml",
    )


def test_reduced_file_path_preserves_supported_suffixes(tmp_path: Path) -> None:
    assert reduced_file_path(tmp_path / "top.dspf", "reduced").name == (
        "top.reduced.dspf"
    )
    assert reduced_file_path(tmp_path / "top.dspf.gz", "red").name == (
        "top.red.dspf.gz"
    )


def test_default_dspf_command_keeps_input_last(tmp_path: Path) -> None:
    cfg = _config(tmp_path, reduction={"ground": "VSS", "reduce_negative": True})
    job = build_reduction_jobs(cfg, cfg.context())[0]

    assert job.command[:3] == ("qreduce", "--type", "dspf")
    assert job.command[3:7] == ("--cpu", "4", "--lic_queue", "1800")
    assert job.command[-2:] == (
        str(tmp_path / "run/top.reduced.dspf"),
        str(tmp_path / "run/top.dspf"),
    )
    assert job.command.index("--out") < len(job.command) - 1
    assert ("--ground", "VSS") == job.command[
        job.command.index("--ground") : job.command.index("--ground") + 2
    ]
    assert reduced_output_paths(cfg, cfg.context()) == (
        tmp_path / "run/top.reduced.dspf",
    )


def test_control_and_delay_modes_emit_only_their_own_accuracy_options(
    tmp_path: Path,
) -> None:
    control = _config(
        tmp_path / "control",
        reduction={"mode": "Reduction Control", "control": "0.65"},
    )
    delay = _config(
        tmp_path / "delay",
        reduction={
            "mode": "Delay and Frequency",
            "delay_rel": "0.02",
            "delay_abs": "2e-12",
            "frequency": "12.5",
        },
    )

    control_command = build_reduction_jobs(control, control.context())[0].command
    delay_command = build_reduction_jobs(delay, delay.context())[0].command

    assert ("--rcontrol", "0.65") == control_command[
        control_command.index("--rcontrol") : control_command.index("--rcontrol") + 2
    ]
    assert "--delay_rel" not in control_command
    assert "--rcontrol" not in delay_command
    assert "--delay_rel" in delay_command
    assert "--delay_abs" in delay_command
    assert "--frequency" in delay_command


def test_selection_mode_requires_an_existing_file(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        reduction={"mode": "Selection File", "selection_file": "missing.sel"},
    )

    with pytest.raises(FileNotFoundError, match="selection file"):
        build_reduction_jobs(cfg, cfg.context())


def test_spice_supports_canonical_device_file(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical.txt"
    canonical.write_text("rpoly\ncmim\n", encoding="utf-8")
    cfg = _config(
        tmp_path,
        output_type="sp",
        reduction={"canonical_device_file": str(canonical)},
    )
    command = build_reduction_jobs(cfg, cfg.context())[0].command

    assert command[1:3] == ("--type", "spice")
    assert ("--canonical_dev", str(canonical)) == command[
        command.index("--canonical_dev") : command.index("--canonical_dev") + 2
    ]


@pytest.mark.parametrize(
    ("view_kind", "qreduce_type"),
    (("smart", "smart_view"), ("extracted", "extracted_view")),
)
def test_native_view_reduction_runs_from_generated_cdslib_directory(
    tmp_path: Path, view_kind: str, qreduce_type: str
) -> None:
    cfg = _config(tmp_path, output_type="view", view_kind=view_kind)
    job = build_reduction_jobs(cfg, cfg.context())[0]

    assert job.command[1:3] == ("--type", qreduce_type)
    assert job.cwd == tmp_path / "run/db/cdl"
    assert job.command[-4:] == (
        "av_extracted_reduced",
        "layout_lib",
        "layout_top",
        "av_extracted",
    )
    assert reduced_view_name(cfg, cfg.context()) == "av_extracted_reduced"


def test_multi_corner_reduction_is_rejected(tmp_path: Path) -> None:
    cfg = _config(tmp_path, corners=["Cmin", "Cmax"])

    with pytest.raises(ValueError, match="disabled for Multiple Corners"):
        build_reduction_jobs(cfg, cfg.context())


def test_multiple_corner_scope_reduction_is_rejected(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg = cfg.replace('extract', 'corner_scope', value="Multiple Corners")

    with pytest.raises(ValueError, match="disabled for Multiple Corners"):
        build_reduction_jobs(cfg, cfg.context())


@pytest.mark.parametrize("output_type", ("spef", "spectre"))
def test_unsupported_file_formats_are_rejected(
    tmp_path: Path, output_type: str
) -> None:
    cfg = _config(tmp_path, output_type=output_type)

    with pytest.raises(ValueError, match="DSPF and SPICE"):
        build_reduction_jobs(cfg, cfg.context())
