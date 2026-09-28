"""Run Cadence Abstract Generator and verify the exported LEF."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .config import LefConfig
from .environment import restore_eda_temp_environment
from .lefcheck import validate_lef
from .replay import ReplayArtifacts, generate_replay


class LefRunner:
    def __init__(
        self,
        cfg: LefConfig,
        *,
        generate_only: bool = False,
        dry_run: bool = False,
    ) -> None:
        self.cfg = cfg
        self.generate_only = generate_only
        self.dry_run = dry_run
        self.artifacts: ReplayArtifacts | None = None
        self.previous_run_dir: Path | None = None

    def run(self) -> int:
        self.cfg.validate_inputs()
        if not (self.generate_only or self.dry_run):
            self._prepare_run_dir()
        self.artifacts = generate_replay(self.cfg)
        command = self._command(
            self.artifacts,
            resolve_executable=not (self.generate_only or self.dry_run),
        )
        self._write_manifest(command)
        self._info(f"Replay: {self.artifacts.replay_file}")
        self._info(f"Command: {shlex.join(command)}")
        if self.generate_only or self.dry_run:
            return 0

        if not self._is_within(self.cfg.output_lef, self.cfg.run_dir):
            self._preserve_existing(self.cfg.output_lef, "Previous output")
        self._preserve_existing(self.artifacts.abstract_log, "Previous Abstract log")
        self._preserve_existing(self.artifacts.lefout_log, "Previous LEF export log")
        self._clear_failure_marker()
        proc = subprocess.Popen(
            command,
            cwd=str(self.cfg.run_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            env=eda_env(),
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            self._write_stdout(line)
        return_code = proc.wait()
        if return_code != 0:
            self._fail(f"Abstract Generator failed with exit code {return_code}")
        if not self.artifacts.abstract_log.is_file():
            self._fail(f"Abstract Generator did not create its log: {self.artifacts.abstract_log}")
        errors = abstract_errors(self.artifacts.abstract_log)
        if errors:
            summary = "; ".join(errors[:3])
            self._fail(
                f"Abstract Generator reported {len(errors)} error(s): {summary}"
            )
        if not self.artifacts.lefout_log.is_file():
            self._fail(f"LEF exporter did not create its log: {self.artifacts.lefout_log}")
        errors = lefout_errors(self.artifacts.lefout_log)
        if errors:
            summary = "; ".join(errors[:3])
            self._fail(f"LEF exporter reported {len(errors)} error(s): {summary}")
        if not self.cfg.output_lef.is_file() or self.cfg.output_lef.stat().st_size == 0:
            self._fail(f"Abstract Generator did not create LEF output: {self.cfg.output_lef}")
        expected_cells = self.cfg.cells if self.cfg.export_geometry else ()
        try:
            validate_lef(
                self.cfg.output_lef,
                expected_version=self.cfg.lef_version,
                expected_cells=expected_cells,
            )
        except ValueError as exc:
            self._fail(f"Invalid LEF output: {exc}")
        self._info(f"Completed: {self.cfg.output_lef}")
        return 0

    def _command(
        self,
        artifacts: ReplayArtifacts,
        *,
        resolve_executable: bool,
    ) -> list[str]:
        executable = self.cfg.abstract_executable
        if "/" in executable:
            path = Path(executable)
            if resolve_executable and (not path.is_file() or not os.access(path, os.X_OK)):
                raise FileNotFoundError(f"Cannot execute Abstract Generator: {path}")
            executable = str(path)
        else:
            resolved = shutil.which(executable)
            if resolve_executable and not resolved:
                raise FileNotFoundError(f"Cannot find Abstract Generator executable: {executable}")
            executable = resolved or executable
        return [
            executable,
            "-nogui",
            "-replay",
            str(artifacts.replay_file),
            "-log",
            str(artifacts.abstract_log),
            "-cdslib",
            str(self.cfg.cds_lib),
        ]

    def _write_manifest(self, command: list[str]) -> None:
        assert self.artifacts is not None
        lines = [
            f"config = {self.cfg.config_path}",
            f"library = {self.cfg.library}",
            f"cells = {', '.join(self.cfg.cells)}",
            f"source_cell_list = {self.cfg.source_cell_list or ''}",
            f"steps = pins:{str(self.cfg.run_pins).lower()}, "
            f"extract:{str(self.cfg.run_extract).lower()}, "
            f"abstract:{str(self.cfg.run_abstract).lower()}",
            f"replay = {self.artifacts.replay_file}",
            f"cell_list = {self.artifacts.cell_list_file}",
            f"abstract_log = {self.artifacts.abstract_log}",
            f"lefout_log = {self.artifacts.lefout_log}",
            f"output_lef = {self.cfg.output_lef}",
            f"previous_run = {self.previous_run_dir or ''}",
            f"command = {shlex.join(command)}",
            "",
        ]
        (self.cfg.run_dir / "lef.manifest").write_text("\n".join(lines), encoding="utf-8")

    def _prepare_run_dir(self) -> None:
        if os.environ.get("LEF_BACKUP_DONE") == "1":
            return
        run_dir = self.cfg.run_dir
        resolved_run_dir = run_dir.resolve()
        if resolved_run_dir.parent == resolved_run_dir:
            raise RuntimeError(
                f"Refusing to replace filesystem root as LEF run directory: {run_dir}"
            )
        if not run_dir.exists():
            run_dir.mkdir(parents=True, exist_ok=True)
            return
        if not run_dir.is_dir():
            raise NotADirectoryError(f"LEF run directory is not a directory: {run_dir}")

        # Relative options/cell-list/config files may live below the run
        # directory. Keep those inputs available after the output tree moves.
        inputs: list[Path] = [self.cfg.config_path]
        if self.cfg.options_file is not None:
            inputs.append(self.cfg.options_file)
        if self.cfg.source_cell_list is not None:
            inputs.append(self.cfg.source_cell_list)
        inputs.append(self.cfg.cds_lib)
        relative_inputs: list[Path] = []
        for path in inputs:
            try:
                relative = path.relative_to(run_dir)
            except ValueError:
                continue
            if path.is_file() and relative not in relative_inputs:
                relative_inputs.append(relative)

        tag = datetime.now().strftime("%m-%d-%H-%M-%S")
        backup = run_dir.with_name(f"{run_dir.name}.{tag}")
        counter = 1
        while backup.exists():
            backup = run_dir.with_name(f"{run_dir.name}.{tag}.{counter}")
            counter += 1
        shutil.move(str(run_dir), str(backup))
        run_dir.mkdir(parents=True, exist_ok=True)
        for relative in relative_inputs:
            source = backup / relative
            destination = run_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        self.previous_run_dir = backup
        self._info(f"Previous run data moved to: {backup}")

    @staticmethod
    def _is_within(path: Path, directory: Path) -> bool:
        try:
            path.relative_to(directory)
        except ValueError:
            return False
        return True

    def _preserve_existing(self, path: Path, label: str) -> None:
        if not path.exists():
            return
        backup = path.with_name(f"{path.name}.prev")
        index = 1
        while backup.exists():
            backup = path.with_name(f"{path.name}.prev.{index}")
            index += 1
        path.rename(backup)
        self._info(f"{label} preserved as: {backup}")

    def _clear_failure_marker(self) -> None:
        marker = self.cfg.run_dir / "log" / "exit-abnormally"
        if marker.exists():
            marker.unlink()

    def _fail(self, message: str) -> None:
        marker = self.cfg.run_dir / "log" / "exit-abnormally"
        marker.write_text(message + "\n", encoding="utf-8")
        raise RuntimeError(message)

    def _info(self, message: str) -> None:
        self._write_stdout(f"[LEF] {message}\n")

    @staticmethod
    def _write_stdout(text: str) -> None:
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except (BrokenPipeError, OSError):
            return


def eda_env() -> dict[str, str]:
    env = restore_eda_temp_environment()
    original_ld = env.get("LEF_ORIG_LD_LIBRARY_PATH")
    if original_ld is not None:
        env["LD_LIBRARY_PATH"] = original_ld
    return env


_CADENCE_ERROR = re.compile(
    r"^(?:\*error\*|error(?:\s+\([^)]+\))?\s*:)", re.IGNORECASE
)
_GDM_ERROR = re.compile(
    r"^\s*\**\s*Error\s+"
    r"\((?P<kind>gdmForkExec|gdmLoadSharedLib|gdmiLoadDMLibrary|"
    r"gdmImportDMSystem)\):(?P<body>.*)$",
    re.IGNORECASE,
)
_OPTIONAL_AIVIVC = re.compile(
    r"(?:aivivcgdmconfig|libgdmaivivc_sh\.so|DM system ['\"]?aivivc['\"]?)",
    re.IGNORECASE,
)
_GDM_CONTACT = re.compile(r"contact the owner of the library", re.IGNORECASE)
_LEFOUT_SUMMARY = re.compile(
    r"^lefout translation completed \(errors:\s*(\d+),\s*warnings:\s*\d+\)\.$",
    re.IGNORECASE,
)


def abstract_errors(log_file: Path) -> list[str]:
    # A library may advertise the optional AIVIVC DM plug-in even when the
    # project environment does not install its helper. Abstract Generator can
    # still complete the OA/LEF flow in that case, so ignore only this known
    # startup diagnostic block. All other Cadence errors remain fatal.
    return cadence_errors(log_file, ignore_optional_aivivc=True)


def cadence_errors(
    log_file: Path, *, ignore_optional_aivivc: bool = False
) -> list[str]:
    errors: list[str] = []
    optional_loader_gap: int | None = None
    for line in log_file.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not _CADENCE_ERROR.match(stripped):
            if optional_loader_gap is not None:
                optional_loader_gap += 1
            continue
        if ignore_optional_aivivc:
            match = _GDM_ERROR.match(stripped)
            if match is not None:
                body = match.group("body")
                if _OPTIONAL_AIVIVC.search(body):
                    optional_loader_gap = (
                        0 if match.group("kind").lower() == "gdmloadsharedlib" else None
                    )
                    continue
                if (
                    optional_loader_gap is not None
                    and optional_loader_gap <= 2
                    and match.group("kind").lower() == "gdmloadsharedlib"
                    and _GDM_CONTACT.search(body)
                ):
                    optional_loader_gap = None
                    continue
            optional_loader_gap = None
        errors.append(stripped)
    return errors


def lefout_errors(log_file: Path) -> list[str]:
    errors = cadence_errors(log_file)
    summaries: list[tuple[int, str]] = []
    for line in log_file.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        match = _LEFOUT_SUMMARY.fullmatch(stripped)
        if match:
            summaries.append((int(match.group(1)), stripped))
    if not summaries:
        errors.append("Missing lefout translation completion summary")
    else:
        errors.extend(summary for count, summary in summaries if count != 0)
    return errors
