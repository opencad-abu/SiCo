"""Shared config fixtures inputs."""

from __future__ import annotations
from pathlib import Path


def _write_request(tmp_path: Path, *, extra: str = "") -> tuple[Path, Path]:
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    cds = source / "cds.lib"
    cds.write_text("DEFINE work ./work\n", encoding="utf-8")
    model = source / "model.lib"
    model.write_text("model", encoding="utf-8")
    request = tmp_path / "request.toml"
    request.write_text(
        f'''format = "sico-mts-netlistor-request"
schema_version = 1

[source]
cds_lib = "{cds}"
library = "work"
cell = "top"
view = "schematic"

[simulator]
dialect = "spectre"

[[models]]
file = "{model}"
section = "tt"
label = "core"

[process_options]
temp = 27
scale = 0.9
gmin = 1e-12

{extra}
''',
        encoding="utf-8",
    )
    return request, cds
