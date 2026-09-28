"""Stage orchestration for the Python RCE backend."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from caddefaults import Defaults
from rcepy.default_files import default_files

from .config import RceConfig
from .gen_qrc import qrc_output_publications
from .gen_starrc import starrc_output_publications
from .generators import generate_all
from .input_preflight import validate_input_locations
from .netlist_postprocess import (
    configured_bus_delimiter_mapping,
    rewrite_netlist_bus_delimiters,
)
from .output_publication import publish_outputs
from .pathutil import restore_eda_temp_environment
from .reduction import (
    ReductionJob,
    build_reduction_jobs,
    reduced_output_paths,
)
from .run_artifacts import (
    archive_completed_config,
    archive_previous_outputs,
    clean_completed_run,
    protected_root_names,
)
from .run_lock import execution_lock
from .run_manifest import write_run_manifest
from .stage_plan import build_extract_stage, build_stages
from .stage_result import IGNORED_LVS_MARKER
from .stage_runner import Stage, StageExecutor
from .textutil import ensure_dirs, write_text
from .xrc import xrc_output_publications
from .xrc_paths import calibre_executable


class RceRunner:
    def __init__(
        self,
        cfg: RceConfig,
        *,
        generate_only: bool = False,
        dry_run: bool = False,
        stop_after: str | None = None,
        lock_token: str | None = None,
    ) -> None:
        self._defaults: Defaults | None = None
        self.cfg = cfg
        self.ctx = cfg.context()
        self.generate_only = generate_only
        self.dry_run = dry_run
        self.stop_after = stop_after
        self.lock_token = lock_token
        self._lock_fds: tuple[int, ...] = ()
        self._initial_root_names: set[str] = set()
        self.generated: dict[str, Path] = {}
        self.reduction_jobs: tuple[ReductionJob, ...] = ()
        self.ignored_lvs_mismatch = False
        self._stage_executor = StageExecutor(
            fail=self._fail,
            info=self._info,
            warn=self._warn,
            output=self._write_stdout,
        )

    def run(self) -> int:
        with execution_lock(self.ctx.run_dir, self.lock_token) as descriptor:
            self._lock_fds = (descriptor,)
            try:
                return self._run_locked()
            finally:
                self._lock_fds = ()

    def prepare(self) -> int:
        """Archive old outputs before the GUI opens the new launch log."""
        with execution_lock(self.ctx.run_dir, self.lock_token):
            self._prepare()
        return 0

    def _run_locked(self) -> int:
        self._defaults = Defaults.load(default_files(self.cfg))
        stages = self._stages()
        extraction_stage_names = [
            stage.name
            for stage in stages
            if stage.name == "extract" or stage.name.startswith("xrc_fmt")
        ]
        final_extraction_stage = (
            extraction_stage_names[-1] if extraction_stage_names else None
        )
        self._validate_stop_after(stages)
        self._prepare()
        self._defaults.save(self.ctx.log_dir)
        try:
            self.reduction_jobs = build_reduction_jobs(self.cfg, self.ctx)
        except (OSError, ValueError) as exc:
            self._fail(f"Reduction setup failed: {exc}")
        self.generated = generate_all(self.cfg, self.ctx, defaults=self._defaults)
        self._write_manifest()
        if self.generate_only:
            self._info("Generated command files only.")
            return 0

        extraction_completed = False
        reached_final_extraction_stage = False
        for stage in stages:
            self._run_stage(stage)
            if stage.name == final_extraction_stage:
                reached_final_extraction_stage = True
                if not self.dry_run:
                    extraction_completed = True
            if self.stop_after == stage.name:
                self._info(f"Stopped after stage: {stage.name}")
                break
        if (
            self.dry_run
            and reached_final_extraction_stage
            and self.stop_after != final_extraction_stage
        ):
            self._reduce_outputs()
        if extraction_completed:
            self._publish_outputs()
            self._postprocess()
            if self.stop_after != final_extraction_stage:
                self._reduce_outputs()
            self._cleanup_run_root()
            self._start_rve()
            self._publish_ignored_lvs_marker()
        self._info("RCE flow completed.")
        return 0

    def _prepare(self) -> None:
        self._validate_input_locations()
        if self.ctx.run_dir.is_dir():
            self._initial_root_names = {entry.name for entry in self.ctx.run_dir.iterdir()}
        self.ignored_lvs_mismatch = False
        self._backup_existing_run_data()
        ensure_dirs(self.ctx.run_dir, self.ctx.log_dir, self.ctx.db_dir, self.ctx.cdl_dir, self.ctx.gds_dir)
        if not self.cfg.is_xrc and self.ctx.input_type != "CCI" and self.ctx.cci_dir:
            Path(self.ctx.cci_dir).mkdir(parents=True, exist_ok=True)
        for marker in (
            self.ctx.log_dir / "exit-abnormally",
            self.ctx.log_dir / IGNORED_LVS_MARKER,
        ):
            if marker.exists():
                marker.unlink()
        cds_lib = self.cfg.path("run", "cds_lib")
        if cds_lib and Path(cds_lib).is_file():
            self._write_cds_lib_include(Path(cds_lib).expanduser().resolve())
        elif self.ctx.input_type in {"OA", "SCH+GDS", "CDL+LAY"}:
            raise FileNotFoundError(f"Cannot access cds.lib: {cds_lib}")

    def _validate_input_locations(self) -> None:
        outputs = {
            path.resolve() for pair in self._output_publications() for path in pair
        }
        try:
            outputs.update(
                path.resolve() for path in reduced_output_paths(self.cfg, self.ctx)
            )
        except ValueError:
            # Reduction validation reports the invalid setting during setup.
            pass
        validate_input_locations(
            self.cfg,
            self.ctx,
            outputs,
            reduced_paths=(),
        )

    def _backup_existing_run_data(self) -> None:
        if os.environ.get("RCE_BACKUP_DONE") == "1":
            return
        outputs = {path for pair in self._output_publications() for path in pair}
        try:
            outputs.update(reduced_output_paths(self.cfg, self.ctx))
        except ValueError:
            # Setup reports invalid reduction settings after archiving the old run.
            pass
        archive_previous_outputs(self.ctx, outputs)

    def _write_cds_lib_include(self, cds_lib: Path) -> None:
        text = f"SOFTINCLUDE {cds_lib}\n"
        write_text(self.ctx.cdl_dir / "cds.lib", text)
        write_text(self.ctx.gds_dir / "cds.lib", text)

    def _stages(self) -> list[Stage]:
        return build_stages(self.cfg, self.ctx, self._output_publications, defaults=self._defaults)

    def _extract_stage(self) -> Stage:
        return build_extract_stage(self.cfg, self.ctx, self._output_publications, defaults=self._defaults)

    def _netlist_output_paths(self) -> tuple[Path, ...]:
        if self.cfg.is_view_output:
            return ()
        return tuple(native for native, _ in self._output_publications())

    def _output_publications(self) -> tuple[tuple[Path, Path], ...]:
        tool = self.cfg.ext_tool.upper()
        if tool in {"QRC", "QUANTUS"}:
            return qrc_output_publications(self.cfg, self.ctx)
        if tool in {"STARRC", "STARXTRACT"}:
            return starrc_output_publications(self.cfg, self.ctx)
        if self.cfg.is_xrc:
            return xrc_output_publications(self.cfg, self.ctx)
        return ()

    def _run_stage(self, stage: Stage) -> None:
        ignored_lvs_mismatch = self._stage_executor.run(
            stage,
            dry_run=self.dry_run,
            environment={} if self.dry_run else self._stage_env(),
            pass_fds=self._lock_fds,
            allow_lvs_mismatch=self.cfg.flag("lvs", "ignore_error"),
        )
        self.ignored_lvs_mismatch = (
            self.ignored_lvs_mismatch or ignored_lvs_mismatch
        )

    def _validate_stop_after(self, stages: list[Stage]) -> None:
        if not self.stop_after:
            return
        names = [stage.name for stage in stages]
        if self.stop_after not in names:
            valid = ", ".join(names)
            raise ValueError(
                f"Stage {self.stop_after!r} is not part of this flow; valid stages: {valid}"
            )

    def _publish_ignored_lvs_marker(self) -> None:
        if not self.ignored_lvs_mismatch:
            return
        write_text(
            self.ctx.log_dir / IGNORED_LVS_MARKER,
            "Extraction completed after LVS "
            "completed INCORRECT; "
            "accepted by lvs.ignore_error=true.\n",
        )

    def _postprocess(self) -> None:
        mapping = configured_bus_delimiter_mapping(self.cfg)
        if mapping is None or self.cfg.is_xrc:
            return
        for output in self.cfg.output_paths(self.ctx):
            if not output.is_file():
                continue
            backup = rewrite_netlist_bus_delimiters(output, mapping)
            if backup:
                self._info(f"Converted bus delimiters in {output}")

    def _publish_outputs(self) -> None:
        try:
            publish_outputs(self._output_publications(), self._info)
        except RuntimeError as exc:
            self._fail(str(exc))

    def _reduce_outputs(self) -> None:
        for job in self.reduction_jobs:
            expected = (job.output_path,) if job.output_path is not None else ()
            self._run_stage(
                Stage(
                    name=job.name,
                    command=list(job.command),
                    cwd=job.cwd,
                    log_file=job.stdout_log,
                    expected_paths=expected,
                    require_nonempty_outputs=bool(expected),
                )
            )

    def _start_rve(self) -> None:
        if not self.cfg.start_rve:
            return
        svdb = Path(self.ctx.svdb_dir)
        if not svdb.is_dir():
            self._warn(f"Cannot start Calibre RVE; SVDB does not exist: {svdb}")
            return
        command = [calibre_executable(), "-rve", str(svdb)]
        self._info(f"Starting RVE: {' '.join(command)}")
        try:
            subprocess.Popen(
                command,
                cwd=str(self.ctx.log_dir),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=self._stage_env(),
                start_new_session=True,
                close_fds=True,
            )
        except OSError as exc:
            self._warn(f"Could not start Calibre RVE: {exc}")

    def _cleanup_run_root(self) -> None:
        output_paths = (*self.cfg.output_paths(self.ctx),
                        *reduced_output_paths(self.cfg, self.ctx))
        keep_names = protected_root_names(
            self.ctx.run_dir,
            managed_names=(self.ctx.db_dir.name, self.ctx.log_dir.name),
            initial_root_names=self._initial_root_names,
            output_paths=output_paths,
        )
        try:
            archived = archive_completed_config(
                self.ctx.run_dir, self.ctx.log_dir, self.cfg.config_path,
            )
            if archived is not None:
                self._write_manifest(config_path=archived)
            clean_completed_run(self.ctx.run_dir, keep_names)
        except OSError as exc:
            self._fail(f"Cannot clean completed run directory {self.ctx.run_dir}: {exc}")

    def _write_manifest(self, *, config_path: Path | None = None) -> None:
        write_run_manifest(self.cfg, self.ctx, self.generated, config_path=config_path)

    def _fail(self, message: str) -> None:
        write_text(self.ctx.log_dir / "exit-abnormally", message + "\n")
        raise RuntimeError(message)

    def _info(self, message: str) -> None:
        self._write_stdout(f"[RCE] {message}\n")

    def _warn(self, message: str) -> None:
        self._write_stdout(f"[RCE][WARN] {message}\n")

    def _stage_env(self) -> dict[str, str]:
        env = restore_eda_temp_environment()
        original_ld = env.get("RCE_ORIG_LD_LIBRARY_PATH")
        if original_ld is not None:
            env["LD_LIBRARY_PATH"] = original_ld
        return env

    def _write_stdout(self, text: str) -> None:
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except BrokenPipeError:
            return
        except OSError:
            return
