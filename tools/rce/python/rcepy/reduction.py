"""Quantus standalone reduction planning for completed RCE outputs."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import re

from .config import DesignContext, RceConfig
from .oa_view import oa_library_definitions, resolve_view_target, view_kind


_FILE_TYPES = {
    "dspf": "dspf",
    "spf": "dspf",
    "sp": "spice",
    "spice": "spice",
    "hspice": "spice",
}
_MODES = {
    "default": "default",
    "reduction control": "control",
    "control": "control",
    "delay and frequency": "delay",
    "delay": "delay",
    "selection file": "selection",
    "selection": "selection",
}
_OUTPUT_TAG = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(frozen=True)
class ReductionJob:
    """One qreduce invocation and the artifacts RCE must validate."""

    name: str
    command: tuple[str, ...]
    cwd: Path
    stdout_log: Path
    output_path: Path | None = None


def reduction_enabled(cfg: RceConfig) -> bool:
    return cfg.flag("reduction", "enabled", default=False)


def reduction_output_tag(cfg: RceConfig) -> str:
    tag = cfg.text("reduction", "output_tag", default="reduced").strip()
    if not _OUTPUT_TAG.fullmatch(tag):
        raise ValueError(
            "reduction.output_tag must contain only letters, digits, and '_'"
        )
    return tag


def reduced_file_path(path: Path, tag: str) -> Path:
    """Insert *tag* before a netlist suffix, preserving optional gzip suffixes."""

    suffixes = path.suffixes
    suffix = "".join(suffixes[-2:] if suffixes[-1:] == [".gz"] else suffixes[-1:])
    stem = path.name[: -len(suffix)] if suffix else path.name
    return path.with_name(f"{stem}.{tag}{suffix}")


def reduced_output_paths(
    cfg: RceConfig, ctx: DesignContext
) -> tuple[Path, ...]:
    if (
        not reduction_enabled(cfg)
        or cfg.is_multi_corner
        or cfg.is_multi_corner_scope
        or cfg.is_view_output
    ):
        return ()
    tag = reduction_output_tag(cfg)
    return tuple(reduced_file_path(path, tag) for path in cfg.output_paths(ctx))


def reduced_view_name(cfg: RceConfig, ctx: DesignContext) -> str:
    if cfg.is_multi_corner or cfg.is_multi_corner_scope:
        raise ValueError("Reduction is disabled for Multiple Corners")
    target = resolve_view_target(cfg, ctx)
    return f"{target.view}_{reduction_output_tag(cfg)}"


def build_reduction_jobs(
    cfg: RceConfig, ctx: DesignContext
) -> tuple[ReductionJob, ...]:
    """Validate reduction settings and build qreduce commands.

    The input remains the extractor's unreduced result. qreduce writes a
    distinct file or OA view, as required by the Quantus command contract.
    """

    if not reduction_enabled(cfg):
        return ()
    if cfg.is_multi_corner or cfg.is_multi_corner_scope:
        raise ValueError("Reduction is disabled for Multiple Corners")

    qreduce_type = _qreduce_type(cfg)
    common = _common_arguments(cfg, qreduce_type)
    executable = os.environ.get("RCE_QREDUCE", "qreduce").strip() or "qreduce"
    jobs: list[ReductionJob] = []

    if cfg.is_view_output:
        oa_library_definitions(cfg)
        target = resolve_view_target(cfg, ctx)
        output_view = reduced_view_name(cfg, ctx)
        report = ctx.log_dir / "qreduce.rpt"
        tool_log = ctx.log_dir / "qreduce.log"
        command = (
            executable,
            "--type",
            qreduce_type,
            *common,
            "--rpt",
            str(report),
            "--log",
            str(tool_log),
            "--out",
            output_view,
            target.library,
            target.cell,
            target.view,
        )
        jobs.append(
            ReductionJob(
                name="reduction",
                command=command,
                cwd=ctx.cdl_dir,
                stdout_log=ctx.log_dir / "qreduce.stdout.log",
            )
        )
        return tuple(jobs)

    inputs = cfg.output_paths(ctx)
    outputs = reduced_output_paths(cfg, ctx)
    for index, (input_path, output_path) in enumerate(zip(inputs, outputs), 1):
        label = "" if len(inputs) == 1 else f".{index:03d}"
        report = ctx.log_dir / f"qreduce{label}.rpt"
        tool_log = ctx.log_dir / f"qreduce{label}.log"
        command = (
            executable,
            "--type",
            qreduce_type,
            *common,
            "--rpt",
            str(report),
            "--log",
            str(tool_log),
            "--out",
            str(output_path),
            str(input_path),
        )
        jobs.append(
            ReductionJob(
                name="reduction" if len(inputs) == 1 else f"reduction_{index:03d}",
                command=command,
                cwd=ctx.run_dir,
                stdout_log=ctx.log_dir / f"qreduce{label}.stdout.log",
                output_path=output_path,
            )
        )
    return tuple(jobs)


def _qreduce_type(cfg: RceConfig) -> str:
    output_type = cfg.output_type.strip().casefold()
    if not cfg.is_view_output:
        try:
            return _FILE_TYPES[output_type]
        except KeyError as exc:
            raise ValueError(
                "Reduction supports RCE output types DSPF and SPICE only for "
                f"file netlists, not {cfg.output_type!r}"
            ) from exc

    tool = cfg.ext_tool.strip().casefold()
    kind = view_kind(cfg)
    if tool not in {"qrc", "quantus"} or kind not in {"smart", "extracted"}:
        raise ValueError(
            "Reduction supports native views only for Quantus Smart View and "
            "Extracted View outputs"
        )
    return "smart_view" if kind == "smart" else "extracted_view"


def _common_arguments(cfg: RceConfig, qreduce_type: str) -> tuple[str, ...]:
    raw_mode = cfg.text("reduction", "mode", default="Default").strip().casefold()
    try:
        mode = _MODES[raw_mode]
    except KeyError as exc:
        raise ValueError(f"Unsupported reduction.mode: {raw_mode!r}") from exc

    cpu = cfg.text("reduction", "cpus", default=cfg.ext_cpus).strip()
    if not cpu.isdigit() or int(cpu) < 1:
        raise ValueError(f"reduction.cpus must be a positive integer, got {cpu!r}")
    arguments: list[str] = ["--cpu", cpu, "--lic_queue", "1800"]

    if mode == "control":
        control = _decimal_option(cfg, "control", "0.5", minimum=0, maximum=1)
        arguments.extend(("--rcontrol", control))
    elif mode == "delay":
        relative = _decimal_option(
            cfg, "delay_rel", "0.05", minimum=0, maximum=1
        )
        absolute = _decimal_option(cfg, "delay_abs", "1e-12", minimum=0)
        frequency = _decimal_option(
            cfg, "frequency", "20", minimum=0, minimum_inclusive=False
        )
        arguments.extend(
            (
                "--delay_rel",
                relative,
                "--delay_abs",
                absolute,
                "--frequency",
                frequency,
            )
        )
    elif mode == "selection":
        selection = cfg.path("reduction", "selection_file")
        if not selection or not Path(selection).is_file():
            raise FileNotFoundError(
                f"Cannot access reduction selection file: {selection or '<empty>'}"
            )
        arguments.extend(("--sel", selection))

    temperature = cfg.text("reduction", "temperature").strip()
    if temperature:
        if qreduce_type not in {"dspf", "smart_view"}:
            raise ValueError(
                "reduction.temperature is supported only for DSPF and Smart View"
            )
        _finite_decimal(temperature, "reduction.temperature")
        arguments.extend(("--temperature", temperature))
    if cfg.flag("reduction", "reduce_negative", default=False):
        arguments.extend(("--reduce_neg_res", "true"))
    ground = cfg.text("reduction", "ground").strip()
    if ground:
        arguments.extend(("--ground", ground))
    canonical = cfg.text("reduction", "canonical_device_file").strip()
    if canonical:
        if qreduce_type != "spice":
            raise ValueError(
                "reduction.canonical_device_file is supported only for SPICE output"
            )
        canonical_path = cfg.path("reduction", "canonical_device_file")
        if not Path(canonical_path).is_file():
            raise FileNotFoundError(
                f"Cannot access canonical device file: {canonical_path}"
            )
        arguments.extend(("--canonical_dev", canonical_path))
    return tuple(arguments)


def _decimal_option(
    cfg: RceConfig,
    key: str,
    default: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    minimum_inclusive: bool = True,
) -> str:
    text = cfg.text("reduction", key, default=default).strip()
    number = _finite_decimal(text, f"reduction.{key}")
    if minimum is not None and (
        number < minimum or (not minimum_inclusive and number == minimum)
    ):
        operator = ">=" if minimum_inclusive else ">"
        raise ValueError(f"reduction.{key} must be {operator} {minimum}")
    if maximum is not None and number > maximum:
        raise ValueError(f"reduction.{key} must be <= {maximum}")
    return text


def _finite_decimal(text: str, option: str) -> Decimal:
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{option} must be numeric, got {text!r}") from exc
    if not number.is_finite():
        raise ValueError(f"{option} must be finite, got {text!r}")
    return number
