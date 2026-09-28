from __future__ import annotations

import stat
from pathlib import Path

import pytest

from lefpy.config import load_config
from lefpy.form_data import render_form_data, write_form_data


def _config(tmp_path: Path) -> Path:
    (tmp_path / "cds.lib").write_text("DEFINE demo ./demo\n", encoding="utf-8")
    (tmp_path / "abstract.options").write_text("; options\n", encoding="utf-8")
    path = tmp_path / "lef.toml"
    path.write_text(
        """
[run]
run_dir = "run/demo.batch.layout"
cds_lib = "cds.lib"
run_type = "LSF Farm"
queue_name = "normal"
server_name = "node08"
cpus = "8"

[input]
library = 'demo"lib'
cells = ["INVX1", "NAND2X1"]

[abstract]
options_file = "abstract.options"
bin = "Core"

[abstract.bin_options]
ExtractSig = true
PinsPowerNames = 'VDD"; absExit()'
AbstractBlockageCoverLayers = ""

[steps]
pins = true
extract = false
abstract = true

[output]
lef_file = "demo.lef"
geometry = true
technology = false
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return path


def test_render_form_data_is_escaped_inert_skill_data(tmp_path: Path) -> None:
    data = render_form_data(load_config(_config(tmp_path)))

    assert data.startswith("lefSetFormLoadData(list(\n")
    assert 'list("library" "demo\\\"lib")' in data
    assert 'list("cells" list("INVX1" "NAND2X1"))' in data
    assert 'list("run_extract" nil)' in data
    assert 'list("run_type" "LSF Farm")' in data
    assert 'list("queue_name" "normal")' in data
    assert 'list("server_name" "node08")' in data
    assert 'list("cpus" "8")' in data
    assert 'list("ExtractSig" "true")' in data
    assert 'list("PinsPowerNames" "VDD\\\"; absExit()")' in data
    assert '\nabsExit()' not in data


def test_write_form_data_uses_exclusive_private_file(tmp_path: Path) -> None:
    cfg = load_config(_config(tmp_path))
    output = tmp_path / "form.il"

    assert write_form_data(cfg, output) == output
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        write_form_data(cfg, output)
