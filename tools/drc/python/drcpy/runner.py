"""Stage orchestration for the DRC-only backend."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from caddefaults import Defaults
from rcepy.default_files import default_files

from cadstage.command_files import GdsCommandOptions, write_streamout_cmd
from cadstage.process import run_stage_command
from rcepy.config import RceConfig
from rcepy.pathutil import restore_eda_temp_environment
from rcepy.textutil import ensure_dirs, write_text

from .generator import generate_drc


@dataclass(frozen=True)
class Stage:
    name: str
    command: list[str]
    cwd: Path
    log_file: Path
    required_file: Path


class DrcRunner:
    def __init__(
        self,
        cfg: RceConfig,
        *,
        generate_only: bool = False,
        dry_run: bool = False,
        stop_after: str | None = None,
    ) -> None:
        self._defaults: Defaults | None = None
        self.cfg = cfg
        self.ctx = cfg.context()
        self.generate_only = generate_only
        self.dry_run = dry_run
        self.stop_after = stop_after
        self.drc_dir = self.ctx.db_dir / "drc"
        self.generated: dict[str, Path] = {}

    def run(self) -> int:
        self._defaults = Defaults.load(default_files(self.cfg, 'drc'))
        stages = self._stages()
        self._prepare()
        self._defaults.save(self.ctx.log_dir)
        self.generated = self._generate()
        self._write_manifest()
        if self.generate_only:
            self._info("Generated command files only.")
            return 0

        for stage in stages:
            self._run_stage(stage)
            if self.stop_after == stage.name:
                self._info(f"Stopped after stage: {stage.name}")
                break
        self._info("DRC flow completed.")
        return 0

    def _prepare(self) -> None:
        if self.ctx.input_type != "OA":
            raise ValueError(f"DRC only supports OA layout input, got: {self.ctx.input_type}")
        self._backup_existing_run_data()
        ensure_dirs(self.ctx.run_dir, self.ctx.log_dir, self.ctx.db_dir, self.ctx.gds_dir, self.drc_dir)
        exit_flag = self.ctx.log_dir / "exit-abnormally"
        if exit_flag.exists():
            exit_flag.unlink()
        cds_lib = self.cfg.text("run", "cds_lib")
        if not cds_lib or not Path(cds_lib).is_file():
            raise FileNotFoundError(f"Cannot access cds.lib: {cds_lib}")
        write_text(self.ctx.gds_dir / "cds.lib", f"SOFTINCLUDE {Path(cds_lib).expanduser().resolve()}\n")

    def _backup_existing_run_data(self) -> None:
        if os.environ.get("DRC_BACKUP_DONE") == "1":
            return
        if not self.ctx.run_dir.is_dir():
            return
        targets = [path for path in (self.ctx.db_dir, self.ctx.log_dir) if path.exists()]
        if not targets:
            return
        tag = datetime.now().strftime("%m-%d-%H-%M-%S")
        for path in targets:
            backup = path.with_name(f"{path.name}.{tag}")
            counter = 1
            while backup.exists():
                backup = path.with_name(f"{path.name}.{tag}.{counter}")
                counter += 1
            shutil.move(str(path), str(backup))

    def _generate(self) -> dict[str, Path]:
        return {
            "gds": write_streamout_cmd(
                self.ctx.gds_dir / "streamout.cmd",
                GdsCommandOptions(
                    layout_lib=self.cfg.text("input", "layout", "lib"),
                    layout_cell=self.ctx.layout_cell,
                    layout_view=self.cfg.text(
                        "input", "layout", "view", default="layout"
                    ),
                    layer_map=self.cfg.path("input", "layout", "layer_map"),
                    output_filename=Path(self.ctx.layout_path).name,
                    replace_bus_bit_char=self.cfg.flag(
                        "options", "replace_bus_bit_char", default=True
                    ),
                ),
                defaults=self._defaults,
            ),
            "drc": generate_drc(self.cfg, self.ctx, defaults=self._defaults),
        }

    def _stages(self) -> list[Stage]:
        mode_arguments = []
        if self.cfg.calibre_run_mode("drc") == "Hier":
            mode_arguments = [
                "-hier",
                "-turbo",
                self.cfg.text("runtime", "cpus", default="1"),
            ]
        gds_command = ["strmout", "-templateFile", "streamout.cmd"]
        if self.cfg.flag("options", "replace_bus_bit_char", default=True):
            gds_command.append("-replaceBusBitChar")
        return [
            Stage(
                "gds",
                gds_command,
                self.ctx.gds_dir,
                self.ctx.log_dir / "strmout.log",
                self.ctx.gds_dir / "streamout.cmd",
            ),
            Stage(
                "drc",
                [
                    "calibre",
                    "-drc",
                    *mode_arguments,
                    str(self.ctx.log_dir / "drc.cal"),
                ],
                self.ctx.run_dir,
                self.ctx.log_dir / "caldrc.log",
                self.ctx.log_dir / "drc.cal",
            ),
        ]

    def _run_stage(self, stage: Stage) -> None:
        self._info(f"Running {stage.name}: {' '.join(stage.command)}")
        rc = run_stage_command(
            stage.command,
            label=stage.name,
            required_file=stage.required_file,
            cwd=stage.cwd,
            log_file=stage.log_file,
            env=eda_env(),
            output_callback=self._write_stdout,
            dry_run=self.dry_run,
            popen=subprocess.Popen,
        )
        if rc != 0:
            self._fail(f"Stage {stage.name} failed with exit code {rc}")

    def _write_manifest(self) -> None:
        lines = [
            f"config = {self.cfg.config_path}",
            f"run_dir = {self.ctx.run_dir}",
            f"input_type = {self.ctx.input_type}",
            f"layout_cell = {self.ctx.layout_cell}",
            f"layout_path = {self.ctx.layout_path}",
            f"run_mode = {self.cfg.calibre_run_mode('drc')}",
            f"results_db = {self.drc_dir / 'cal_drc.out'}",
            "",
            "[generated]",
        ]
        for name, path in sorted(self.generated.items()):
            lines.append(f"{name} = {path}")
        write_text(self.ctx.log_dir / "drc.manifest", "\n".join(lines) + "\n")

    def _fail(self, message: str) -> None:
        write_text(self.ctx.log_dir / "exit-abnormally", message + "\n")
        raise RuntimeError(message)

    def _info(self, message: str) -> None:
        self._write_stdout(f"[DRC] {message}\n")

    def _write_stdout(self, text: str) -> None:
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except (BrokenPipeError, OSError):
            return


def open_rve(cfg: RceConfig, *, dry_run: bool = False) -> int:
    ctx = cfg.context()
    results_db = ctx.db_dir / "drc" / "cal_drc.out"
    command = ["calibre", "-rve", str(results_db)]
    print(f"[DRC] Running report: {' '.join(command)}")
    if dry_run:
        return 0
    if not results_db.is_file():
        raise FileNotFoundError(f"Cannot access DRC results database: {results_db}")
    subprocess.Popen(
        command,
        cwd=str(ctx.run_dir),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=eda_env(),
        start_new_session=True,
        close_fds=True,
    )
    return 0


def eda_env() -> dict[str, str]:
    env = restore_eda_temp_environment()
    if "DRC_ORIG_LD_LIBRARY_PATH" in env:
        env["LD_LIBRARY_PATH"] = env["DRC_ORIG_LD_LIBRARY_PATH"]
    return env
