"""Single-stage CDL/GDS export orchestration."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from caddefaults import Defaults
from rcepy.default_files import default_files

from cadstage.command_files import (
    CdlCommandOptions,
    GdsCommandOptions,
    write_cdl_env,
    write_streamout_cmd,
)
from cadstage.backup import backup_existing, backup_tag
from cadstage.process import publish_stage_output, run_stage_command
from rcepy.config import RceConfig
from rcepy.textutil import ensure_dirs, write_text

from .runner import archive_run_config, eda_env


@dataclass(frozen=True)
class SingleStageSpec:
    name: str
    label: str
    command: list[str]
    cwd: Path
    log_file: Path
    required_file: Path
    produced_file: Path
    output_file: Path
    data_dir: Path
    manifest_name: str


class SingleStageRunner:
    def __init__(
        self,
        cfg: RceConfig,
        stage: str,
        *,
        generate_only: bool = False,
        dry_run: bool = False,
    ) -> None:
        if stage not in {"cdl", "gds"}:
            raise ValueError(f"Unsupported single stage: {stage}")
        self._defaults: Defaults | None = None
        self.cfg = cfg
        self.ctx = cfg.context()
        self.stage = stage
        self.generate_only = generate_only
        self.dry_run = dry_run
        self.generated: Path | None = None

    def _replace_bus_bit_char(self) -> bool:
        """Read the stage option while retaining the historical enabled default."""
        return self.cfg.flag("options", "replace_bus_bit_char", default=True)

    def run(self) -> int:
        self._defaults = Defaults.load(default_files(self.cfg, self.stage))
        spec = self._spec()
        self._prepare(spec)
        self._defaults.save(self.ctx.log_dir)
        self.generated = self._generate()
        archive_run_config(self.cfg)
        self._write_manifest(spec)
        if self.generate_only:
            self._info(f"Generated {spec.label} command file only.")
            return 0
        self._run_stage(spec)
        if not self.dry_run:
            self._publish_output(spec)
        self._info(f"{spec.label} completed.")
        return 0

    def _spec(self) -> SingleStageSpec:
        if self.stage == "cdl":
            return SingleStageSpec(
                "cdl",
                "Export CDL",
                ["si", "-batch"],
                self.ctx.cdl_dir,
                self.ctx.log_dir / "si.log",
                self.ctx.cdl_dir / "si.env",
                self.ctx.cdl_dir / f"{self.ctx.source_cell}.cdl",
                self.ctx.run_dir / f"{self.ctx.source_cell}.cdl",
                self.ctx.cdl_dir,
                "export_cdl.manifest",
            )
        command = ["strmout", "-templateFile", "streamout.cmd"]
        if self._replace_bus_bit_char():
            command.append("-replaceBusBitChar")
        return SingleStageSpec(
            "gds",
            "Stream GDS",
            command,
            self.ctx.gds_dir,
            self.ctx.log_dir / "strmout.log",
            self.ctx.gds_dir / "streamout.cmd",
            self.ctx.gds_dir / f"{self.ctx.layout_cell}.gds",
            self.ctx.run_dir / f"{self.ctx.layout_cell}.gds",
            self.ctx.gds_dir,
            "stream_gds.manifest",
        )

    def _prepare(self, spec: SingleStageSpec) -> None:
        self._backup_stage_data(spec)
        ensure_dirs(self.ctx.run_dir, self.ctx.log_dir, self.ctx.db_dir, spec.data_dir)
        exit_flag = self.ctx.log_dir / "exit-abnormally"
        if exit_flag.exists():
            exit_flag.unlink()
        cds_lib = self.cfg.text("run", "cds_lib")
        if not cds_lib or not Path(cds_lib).is_file():
            raise FileNotFoundError(f"Cannot access cds.lib: {cds_lib}")
        self._write_cds_lib_include(Path(cds_lib).expanduser().resolve(), spec)

    def _backup_stage_data(self, spec: SingleStageSpec) -> None:
        if os.environ.get("LVS_BACKUP_DONE") == "1":
            return
        if not self.ctx.run_dir.is_dir():
            return
        tag = backup_tag()
        config_path = self.cfg.config_path.resolve()
        # The supplied configuration can itself be the previous log snapshot.
        log_files = [path for path in self._stage_log_files(spec)
                     if path.resolve() != config_path]
        backup_existing(
            (
                spec.data_dir,
                spec.output_file,
                *log_files,
            ),
            tag,
        )

    def _stage_log_files(self, spec: SingleStageSpec) -> list[Path]:
        default_name = "cdl.env" if spec.name == "cdl" else "streamout.options"
        default_records = [
            self.ctx.log_dir / "flow-defaults" / (default_name + suffix)
            for suffix in ("", ".json")
        ]
        if spec.name == "cdl":
            return [
                *default_records,
                self.ctx.log_dir / "si.log",
                self.ctx.log_dir / "export_cdl.launch.log",
                self.ctx.log_dir / spec.manifest_name,
                self.ctx.log_dir / "export_cdl.toml",
            ]
        return [
            *default_records,
            self.ctx.log_dir / "strmout.log",
            self.ctx.log_dir / "stream_gds.launch.log",
            self.ctx.log_dir / spec.manifest_name,
            self.ctx.log_dir / "stream_gds.toml",
        ]

    def _write_cds_lib_include(self, cds_lib: Path, spec: SingleStageSpec) -> None:
        write_text(spec.data_dir / "cds.lib", f"SOFTINCLUDE {cds_lib}\n")

    def _generate(self) -> Path:
        if self.stage == "cdl":
            return write_cdl_env(
                self.ctx.cdl_dir / "si.env",
                CdlCommandOptions(
                    schematic_lib=self.cfg.text("input", "schematic", "lib"),
                    schematic_cell=self.cfg.text("input", "schematic", "cell"),
                    schematic_view=self.cfg.text("input", "schematic", "view"),
                    source_filename=Path(self.ctx.source_path).name,
                    header_file=self.cfg.path(
                        "input", "schematic", "cdl_header_file"
                    ),
                    replace_angle_brackets=self._replace_bus_bit_char(),
                ),
                defaults=self._defaults,
            )
        return write_streamout_cmd(
            self.ctx.gds_dir / "streamout.cmd",
            GdsCommandOptions(
                layout_lib=self.cfg.text("input", "layout", "lib"),
                layout_cell=self.ctx.layout_cell,
                layout_view=self.cfg.text(
                    "input", "layout", "view", default="layout"
                ),
                layer_map=self.cfg.path("input", "layout", "layer_map"),
                output_filename=Path(self.ctx.layout_path).name,
                replace_bus_bit_char=self._replace_bus_bit_char(),
            ),
            defaults=self._defaults,
        )

    def _run_stage(self, spec: SingleStageSpec) -> None:
        self._info(f"Running {spec.label}: {' '.join(spec.command)}")
        if spec.name == "cdl":
            (spec.cwd / "control").touch()
        rc = run_stage_command(
            spec.command,
            label=spec.label,
            required_file=spec.required_file,
            cwd=spec.cwd,
            log_file=spec.log_file,
            env=eda_env(),
            output_callback=self._write_stdout,
            dry_run=self.dry_run,
            popen=subprocess.Popen,
        )
        if rc != 0:
            self._fail(f"{spec.label} failed with exit code {rc}")

    def _publish_output(self, spec: SingleStageSpec) -> None:
        try:
            publish_stage_output(
                spec.produced_file, spec.output_file, label=spec.label
            )
        except RuntimeError as exc:
            self._fail(str(exc))

    def _write_manifest(self, spec: SingleStageSpec) -> None:
        lines = [
            f"config = {self.cfg.config_path}",
            f"run_dir = {self.ctx.run_dir}",
            f"stage = {spec.name}",
            f"generated = {self.generated}",
            f"output = {spec.output_file}",
            "",
        ]
        write_text(self.ctx.log_dir / spec.manifest_name, "\n".join(lines))

    def _fail(self, message: str) -> None:
        write_text(self.ctx.log_dir / "exit-abnormally", message + "\n")
        raise RuntimeError(message)

    def _info(self, message: str) -> None:
        self._write_stdout(f"[LVS] {message}\n")

    def _write_stdout(self, text: str) -> None:
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except (BrokenPipeError, OSError):
            return
