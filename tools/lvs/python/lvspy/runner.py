"""Stage orchestration for the LVS-only backend."""

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

from cadgui.transfer import atomic_publish_text
from cadstage.command_files import (
    CdlCommandOptions,
    GdsCommandOptions,
    write_cdl_env,
    write_streamout_cmd,
)
from cadstage.process import run_stage_command
from rcepy.config import RceConfig
from rcepy.gen_lvs import generate_lvs, lvs_hcell_arguments
from rcepy.pathutil import restore_eda_temp_environment
from rcepy.textutil import ensure_dirs, write_text


INPUTS_NEED_CDL = {"OA", "SCH+GDS"}
INPUTS_NEED_GDS = {"OA", "CDL+LAY"}
INPUTS_NEED_LVS = {"OA", "SCH+GDS", "CDL+LAY", "CDL+GDS"}
RUN_ROOT_LOG_ARTIFACTS = ("erc.db", "erc.rep")


def archive_run_config(cfg: RceConfig) -> None:
    """Snapshot a run-root TOML without consuming the reusable input path."""
    ctx = cfg.context()
    config_path = cfg.config_path
    try:
        in_run_root = config_path.parent.resolve() == ctx.run_dir.resolve()
    except OSError:
        in_run_root = False
    if not in_run_root or not config_path.is_file():
        return

    archived_config = ctx.log_dir / config_path.name
    with config_path.open(encoding="utf-8", newline="") as source:
        atomic_publish_text(archived_config, source.read())


@dataclass(frozen=True)
class Stage:
    name: str
    command: list[str]
    cwd: Path
    log_file: Path
    required_file: Path | None = None
    check: str | None = None


class LvsRunner:
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
        self.generated: dict[str, Path] = {}

    def _replace_bus_bit_char(self) -> bool:
        """Read the stage option while retaining the historical enabled default."""
        return self.cfg.flag("options", "replace_bus_bit_char", default=True)

    def run(self) -> int:
        self._defaults = Defaults.load(default_files(self.cfg, 'lvs'))
        stages = self._stages()
        self._prepare()
        self._defaults.save(self.ctx.log_dir)
        self.generated = self._generate()
        archive_run_config(self.cfg)
        self._write_manifest()
        if self.generate_only:
            self._info("Generated command files only.")
            return 0

        try:
            for stage in stages:
                self._run_stage(stage)
                if self.stop_after == stage.name:
                    self._info(f"Stopped after stage: {stage.name}")
                    break
        finally:
            self._collect_run_root_log_artifacts()
        self._info("LVS flow completed.")
        return 0

    def _prepare(self) -> None:
        self._backup_existing_run_data()
        ensure_dirs(self.ctx.run_dir, self.ctx.log_dir, self.ctx.db_dir, self.ctx.cdl_dir, self.ctx.gds_dir)
        exit_flag = self.ctx.log_dir / "exit-abnormally"
        if exit_flag.exists():
            exit_flag.unlink()
        cds_lib = self.cfg.text("run", "cds_lib")
        if cds_lib and Path(cds_lib).is_file():
            self._write_cds_lib_include(Path(cds_lib).expanduser().resolve())
        elif self.ctx.input_type in {"OA", "SCH+GDS", "CDL+LAY"}:
            raise FileNotFoundError(f"Cannot access cds.lib: {cds_lib}")

    def _backup_existing_run_data(self) -> None:
        if os.environ.get("LVS_BACKUP_DONE") == "1":
            return
        if not self.ctx.run_dir.is_dir():
            return
        targets = [path for path in (self.ctx.db_dir, self.ctx.log_dir) if path.exists()]
        root_artifacts = [
            self.ctx.run_dir / name
            for name in RUN_ROOT_LOG_ARTIFACTS
            if (self.ctx.run_dir / name).is_file()
        ]
        if not targets and not root_artifacts:
            return
        tag = datetime.now().strftime("%m-%d-%H-%M-%S")
        log_backup: Path | None = None
        for path in targets:
            backup = self._available_backup_path(path, tag)
            shutil.move(str(path), str(backup))
            if path == self.ctx.log_dir:
                log_backup = backup
        if root_artifacts:
            if log_backup is None:
                log_backup = self._available_backup_path(self.ctx.log_dir, tag)
                log_backup.mkdir(parents=True)
            for path in root_artifacts:
                shutil.move(str(path), str(log_backup / path.name))

    @staticmethod
    def _available_backup_path(path: Path, tag: str) -> Path:
        backup = path.with_name(f"{path.name}.{tag}")
        counter = 1
        while backup.exists():
            backup = path.with_name(f"{path.name}.{tag}.{counter}")
            counter += 1
        return backup

    def _collect_run_root_log_artifacts(self) -> None:
        for name in RUN_ROOT_LOG_ARTIFACTS:
            source = self.ctx.run_dir / name
            if source.is_file():
                source.replace(self.ctx.log_dir / name)

    def _write_cds_lib_include(self, cds_lib: Path) -> None:
        text = f"SOFTINCLUDE {cds_lib}\n"
        write_text(self.ctx.cdl_dir / "cds.lib", text)
        write_text(self.ctx.gds_dir / "cds.lib", text)

    def _enabled_stages(self) -> list[str]:
        input_type = self.ctx.input_type
        if input_type == "SVDB":
            return []
        if input_type not in INPUTS_NEED_LVS:
            raise ValueError(f"Unsupported LVS input type: {input_type}")
        stages: list[str] = []
        if input_type in INPUTS_NEED_CDL:
            stages.append("cdl")
        if input_type in INPUTS_NEED_GDS:
            stages.append("gds")
        stages.append("lvs")
        return stages

    def _generate(self) -> dict[str, Path]:
        outputs: dict[str, Path] = {}
        for stage in self._enabled_stages():
            if stage == "cdl":
                outputs["cdl"] = write_cdl_env(
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
            elif stage == "gds":
                outputs["gds"] = write_streamout_cmd(
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
            elif stage == "lvs":
                outputs["lvs"] = generate_lvs(self.cfg, self.ctx, standalone=True, defaults=self._defaults)
        return outputs

    def _stages(self) -> list[Stage]:
        stages: list[Stage] = []
        enabled = self._enabled_stages()
        if "cdl" in enabled:
            stages.append(
                Stage(
                    "cdl",
                    ["si", "-batch"],
                    self.ctx.cdl_dir,
                    self.ctx.log_dir / "si.log",
                    self.ctx.cdl_dir / "si.env",
                )
            )
        if "gds" in enabled:
            command = ["strmout", "-templateFile", "streamout.cmd"]
            if self._replace_bus_bit_char():
                command.append("-replaceBusBitChar")
            stages.append(
                Stage(
                    "gds",
                    command,
                    self.ctx.gds_dir,
                    self.ctx.log_dir / "strmout.log",
                    self.ctx.gds_dir / "streamout.cmd",
                )
            )
        if "lvs" in enabled:
            mode_arguments = []
            hcell_arguments = []
            if self.cfg.calibre_run_mode("lvs") == "Hier":
                mode_arguments = ["-turbo", self.cfg.lvs_cpus, "-hier"]
                hcell_arguments = lvs_hcell_arguments(self.cfg)
            stages.append(
                Stage(
                    "lvs",
                    [
                        "calibre",
                        "-lvs",
                        *mode_arguments,
                        *hcell_arguments,
                        str(self.ctx.log_dir / "lvs.cal"),
                    ],
                    self.ctx.run_dir,
                    self.ctx.log_dir / "callvs.log",
                    self.ctx.log_dir / "lvs.cal",
                    check="lvs",
                )
            )
        return stages

    def _run_stage(self, stage: Stage) -> None:
        self._info(f"Running {stage.name}: {' '.join(stage.command)}")
        if stage.name == "cdl":
            (stage.cwd / "control").touch()
        rc = run_stage_command(
            stage.command,
            label=stage.name,
            required_file=stage.required_file,
            cwd=stage.cwd,
            log_file=stage.log_file,
            env=self._stage_env(),
            output_callback=self._write_stdout,
            dry_run=self.dry_run,
            popen=subprocess.Popen,
        )
        if rc != 0:
            self._fail(f"Stage {stage.name} failed with exit code {rc}")
        if stage.check == "lvs":
            self._check_lvs(stage.log_file)

    def _check_lvs(self, log_file: Path) -> None:
        if self.cfg.flag("lvs", "ignore_error"):
            return
        text = log_file.read_text(errors="ignore") if log_file.is_file() else ""
        if "LVS completed. CORRECT." not in text:
            self._fail("LVS did not complete cleanly. Set lvs.ignore_error=true only for debug runs.")

    def _write_manifest(self) -> None:
        lines = [
            f"config = {self.cfg.config_path}",
            f"run_dir = {self.ctx.run_dir}",
            f"input_type = {self.ctx.input_type}",
            f"top_cell = {self.ctx.top_cell}",
            f"lvs_tool = {self.cfg.lvs_tool}",
            f"run_mode = {self.cfg.calibre_run_mode('lvs')}",
            f"svdb_dir = {self.ctx.svdb_dir}",
            "",
            "[generated]",
        ]
        for name, path in sorted(self.generated.items()):
            lines.append(f"{name} = {path}")
        write_text(self.ctx.log_dir / "lvs.manifest", "\n".join(lines) + "\n")

    def _fail(self, message: str) -> None:
        write_text(self.ctx.log_dir / "exit-abnormally", message + "\n")
        raise RuntimeError(message)

    def _info(self, message: str) -> None:
        self._write_stdout(f"[LVS] {message}\n")

    def _stage_env(self) -> dict[str, str]:
        return eda_env()

    def _write_stdout(self, text: str) -> None:
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except (BrokenPipeError, OSError):
            return


def open_rve(cfg: RceConfig, *, dry_run: bool = False) -> int:
    ctx = cfg.context()
    svdb = Path(ctx.svdb_dir)
    command = ["calibre", "-rve", str(svdb)]
    print(f"[LVS] Running report: {' '.join(command)}")
    if dry_run:
        return 0
    if not svdb.exists():
        raise FileNotFoundError(f"Cannot access SVDB: {svdb}")
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
    if "LVS_ORIG_LD_LIBRARY_PATH" in env:
        env["LD_LIBRARY_PATH"] = env["LVS_ORIG_LD_LIBRARY_PATH"]
    return env
