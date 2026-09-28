from __future__ import annotations

import sys
from pathlib import Path

import pytest


PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.config import RceConfig  # noqa: E402
from rcepy.gen_qrc import generate_qrc  # noqa: E402


def _config(tmp_path: Path, tech_dir: Path, corner: str = "Cmax") -> RceConfig:
    return RceConfig(
        raw={
            "run": {"run_dir": str(tmp_path / "run")},
            "input": {
                "type": "CCI",
                "cci": {"dir": str(tmp_path / "input.cci"), "cell": "top"},
            },
            "lvs": {"tool": "Calibre"},
            "extract": {
                "tool": "QRC",
                "tech_dir": str(tech_dir),
                "corner": corner,
                "temperature": "25",
                "rc_type": "R+Cg+Cc",
                "output_type": "dspf",
            },
            "runtime": {"ext_cpus": "1"},
            "netlist": {},
        },
        config_path=tmp_path / "rce.toml",
    )


def _write_tech(directory: Path, marker: str = "qrcTechFile") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / marker).write_text("technology\n", encoding="utf-8")
    return directory


def _generated_tech_dir(cfg: RceConfig) -> str:
    command = generate_qrc(cfg, cfg.context()).read_text(encoding="utf-8")
    marker = '-technology_directory "'
    return command.split(marker, 1)[1].split('"', 1)[0]


def test_qrc_prefers_exact_corner_and_preserves_directory_case(tmp_path: Path) -> None:
    process_dir = tmp_path / "QRC"
    exact = _write_tech(process_dir / "Cmax")
    _write_tech(process_dir / "cmax")

    assert _generated_tech_dir(_config(tmp_path, process_dir, "Cmax")) == str(exact)


def test_qrc_falls_back_to_case_insensitive_actual_directory(tmp_path: Path) -> None:
    process_dir = tmp_path / "QRC"
    actual = _write_tech(process_dir / "Cmax")

    assert _generated_tech_dir(_config(tmp_path, process_dir, "cMAX")) == str(actual)


@pytest.mark.parametrize("marker", ["qrcTechFile", "qrc.tch", "cap_coeff.dat"])
def test_qrc_accepts_each_technology_marker_and_direct_corner(
    tmp_path: Path, marker: str
) -> None:
    corner_dir = _write_tech(tmp_path / marker / "Typ", marker)

    assert _generated_tech_dir(
        _config(tmp_path / marker, corner_dir, "different-corner")
    ) == str(corner_dir)


def test_qrc_invalid_corner_lists_available_corners(tmp_path: Path) -> None:
    process_dir = tmp_path / "QRC"
    for name, marker in (
        ("Cmax", "qrcTechFile"),
        ("Cmin", "qrc.tch"),
        ("Typ", "cap_coeff.dat"),
    ):
        _write_tech(process_dir / name, marker)
    cfg = _config(tmp_path, process_dir, "RCmax")

    with pytest.raises(FileNotFoundError) as exc_info:
        generate_qrc(cfg, cfg.context())

    message = str(exc_info.value)
    assert "RCmax" in message
    assert str(process_dir) in message
    assert "qrcTechFile, qrc.tch, cap_coeff.dat" in message
    assert "Available QRC corners: Cmax, Cmin, Typ" in message


def test_qrc_missing_process_directory_fails_before_command_generation(
    tmp_path: Path,
) -> None:
    process_dir = tmp_path / "missing-QRC"
    cfg = _config(tmp_path, process_dir, "Typ")

    with pytest.raises(FileNotFoundError, match="Available QRC corners: <none>"):
        generate_qrc(cfg, cfg.context())

    assert not (tmp_path / "run/log/qrc.ccl").exists()


def test_qrc_requires_process_directory(tmp_path: Path) -> None:
    cfg = _config(tmp_path, tmp_path / "unused")
    cfg = cfg.replace('extract', 'tech_dir', value="")

    with pytest.raises(ValueError, match=r"extract[.]tech_dir.*Process Directory"):
        generate_qrc(cfg, cfg.context())
