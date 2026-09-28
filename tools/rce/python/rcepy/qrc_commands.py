"""Render Quantus extraction commands and selected-net modes."""

from __future__ import annotations

from .qrc_defaults import QrcDefaults
from .config import DesignContext, RceConfig
from .textutil import q, write_text


def qrc_extract_commands(cfg: RceConfig, ctx: DesignContext, rc_type: str, baseline: QrcDefaults) -> tuple[str, str]:
    extract_options = " ".join(baseline.options("extract"))
    extract_prefix = "extract" + (" " + extract_options if extract_options else "")
    full_extract = ""
    net_extract = ""
    mode, nets = cfg.net_selection()
    if mode is not None:
        nets_path = ctx.log_dir / "nets"
        write_text(
            nets_path,
            "".join(
                net.replace("<", "[").replace(">", "]") + "\n"
                for net in nets
            ),
        )
        if mode == "exclude":
            full_extract = f'{extract_prefix} -selection "all" -type {rc_type}'
            net_extract = f'extract -selection "nets_file {nets_path}" -type "none"'
        else:
            net_extract = f'{extract_prefix} -selection "nets_file {nets_path}" -type "{rc_type}"'
    else:
        full_extract = f'{extract_prefix} -selection "all" -type {rc_type}'
    return full_extract, net_extract


def qrc_lines(
    cfg: RceConfig,
    ctx: DesignContext,
    technology_options: list[str],
    input_db_options: list[str],
    output_db_options: list[str],
    output_file: str,
    filter_c: str,
    filter_r: str,
    output_name_space: str,
    cell_options: list[str],
    baseline: QrcDefaults,
) -> list[str]:
    lines: list[str] = []
    lines.extend(_qrc_command("distributed_processing", [*baseline.options("distributed_processing"), f"-multi_cpu {cfg.ext_cpus}"]))
    lines.append("")
    lines.extend(
        _qrc_command(
            "process_technology",
            [*baseline.options("process_technology", "multi_corner" if cfg.is_multi_corner else ""), *technology_options],
        )
    )
    lines.append("")
    lines.extend(
        _qrc_command(
            "capacitance",
            baseline.options("capacitance"),
        )
    )
    lines.append("")
    lines.extend(_qrc_command("filter_cap", baseline.options("filter_cap")))
    coupling_defaults = " ".join(baseline.options("filter_coupling_cap"))
    if coupling_defaults:
        filter_c = (filter_c + " " + coupling_defaults) if filter_c else "filter_coupling_cap " + coupling_defaults
    if filter_c:
        lines.append("")
        lines.append(filter_c)
    lines.append("")
    lines.extend(_qrc_command("filter_res", [*baseline.options("filter_res"), filter_r]))
    lines.append("")
    lines.extend(
        _qrc_command(
            "extraction_setup",
            [
                *baseline.options("extraction_setup", "block_cells" if cell_options else ""),
                *cell_options,
            ],
        )
    )
    lines.append("")
    lines.extend(_qrc_command("input_db", [*baseline.options("input_db"), *input_db_options]))
    lines.append("")
    lines.extend(_qrc_command("output_db", output_db_options))
    lines.append("")
    lines.extend(
        _qrc_command(
            "output_setup",
            [
                *baseline.options("output_setup"),
                f'-net_name_space "{output_name_space}"',
                f'-directory_name "{q(str(ctx.db_dir))}"',
                output_file,
            ],
        )
    )
    lines.append("")
    lines.extend(_qrc_command("log_file", [*baseline.options("log_file"), f'-file_name "{q(str(ctx.log_dir / "qrc.log"))}"']))
    return lines


def _qrc_command(name: str, options: list[str]) -> list[str]:
    rendered = [option.strip() for option in options if str(option).strip()]
    if not rendered:
        return []
    lines = [f"{name} \\"]
    for index, option in enumerate(rendered):
        suffix = " \\" if index < len(rendered) - 1 else ""
        lines.append(f"    {option}{suffix}")
    return lines
