"""Synthetic process inputs; no foundry device data is stored in tests."""

from pathlib import Path

from rcepy.config import RceConfig


def write(path: Path, content: str = "test\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def config(tmp_path: Path, tool: str = "QRC", *, mode: bool = True) -> RceConfig:
    tech = tmp_path / "process files"
    write(tech / "Tech/typical/qrcTechFile")
    write(tech / "starrcxt.nxtgrd")
    write(tech / "starrcxt_mapping")
    write(tech / "star_cmd", "MULTIGATE_MODELS: YES\nDPT: YES\n"
          "LPE_PARAM: first RC 0\nLPE_PARAM: second RC 1\n"
          "LPE_DEVICES: first synthetic_n\nLPE_DEVICES: second synthetic_p\n"
          "CALIBRE_OPTIONAL_DEVICE_PIN_FILE: pin_file.txt\n"
          "DENSITY_OUTSIDE_BLOCK: 0.5\nBLOCK: wrong_design\nREDUCTION: NO\n")
    write(tech / "pin_file.txt")
    write(tech / "capgen.cmd", "Techgen -compilation -lvs_query_directory ../query_output "
          "-lvs_run_name Example -blocking gate:0.001,poly,active .\n")
    write(tech / "layer_setup")
    write(tech / "lpe_confile")
    write(tech / "hcell_qci", "synthetic_*\n")
    write(tech / "source.added", ".SUBCKT synthetic_device a b\n.ENDS\n")
    deck = write(tech / "lvs.cal", "LAYOUT PRIMARY example\nSOURCE PATH example.cdl\n"
                 "MASK SVDB DIRECTORY example_svdb QUERY CCI SI\nLVS REPORT example.rep\n"
                 "LVS REPORT MAXIMUM 1000\nINCLUDE ./DFM/rules\n")
    write(tech.parent / "common_file/DFM/rules", "INCLUDE ./DFM/device.encrypt\n")
    write(tech.parent / "common_file/DFM/device.encrypt", "encrypted fixture\n")
    return RceConfig({
        "run": {"run_dir": str(tmp_path / "run")},
        "input": {"type": "CDL+GDS",
                  "cdl": {"file": str(write(tmp_path / "design.cdl")), "cell": "top"},
                  "gds": {"file": str(write(tmp_path / "design.gds")), "cell": "top"}},
        "lvs": {"runset_file": str(deck)},
        "extract": {"tool": tool, "tech_dir": str(tech), "finfet_flow": mode,
                    "corners": ["typical"] if tool == "QRC" else [],
                    "temperature": "25", "rc_type": "R+Cg+Cc", "output_type": "dspf"},
    }, tmp_path / "rce.toml")
