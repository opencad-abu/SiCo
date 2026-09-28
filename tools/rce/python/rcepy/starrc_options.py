"""Render StarRC netlist, selection and filter options."""

from __future__ import annotations

from .config import DesignContext, RceConfig
from .filter_units import (
    femtofarads_to_farads,
    format_decimal,
    nonnegative_decimal,
    percent_to_ratio,
)
from .oa_view import view_kind
from .starrc_inputs import require_starrc_file
from .textutil import write_text


def star_yes_no(value: object, default: str = "YES") -> str:
    if isinstance(value, bool):
        return "YES" if value else "NO"
    text = str(value).strip().upper()
    if text in {"TRUE", "T", "1"}:
        return "YES"
    if text in {"FALSE", "F", "0"}:
        return "NO"
    return text or default


def starrc_netlist_format(cfg: RceConfig) -> str:
    if cfg.is_view_output:
        if view_kind(cfg) != "oa":
            raise ValueError("StarRC view output requires the native OA view kind")
        return "OA"

    output_type = cfg.output_type.strip().casefold()
    try:
        return STARRC_NETLIST_FORMATS[output_type]
    except KeyError as exc:
        supported = ", ".join(STARRC_NETLIST_FORMATS)
        raise ValueError(
            f"Unsupported StarRC output type {cfg.output_type!r}; "
            "StarRC NETLIST_FORMAT does not support ordinary SPICE output. "
            f"Use one of: {supported}"
        ) from exc


def starrc_hierarchical_separator(cfg: RceConfig) -> str:
    if not cfg.flag(
        "netlist",
        "hierarchy_delimiter_enable",
    ):
        return "/"

    separator = cfg.text(
        "netlist",
        "hierarchy_delimiter",
    ).strip()
    if separator not in STARRC_HIERARCHICAL_SEPARATORS:
        supported = "', '".join(sorted(STARRC_HIERARCHICAL_SEPARATORS))
        raise ValueError(
            f"Unsupported StarRC hierarchy delimiter {separator!r}; "
            f"use one of: '{supported}'"
        )
    return separator


def starrc_pin_order_file(
    cfg: RceConfig,
    ctx: DesignContext,
    netlist_format: str,
) -> str | None:
    if not cfg.flag(
        "netlist", "pin_order_enable"
    ):
        return None

    if netlist_format != "SPF":
        raise ValueError(
            "StarRC User Pin Order uses SPICE_SUBCKT_FILE, which is supported "
            "only for DSPF/SPF output; disable netlist.pin_order_enable or "
            "select DSPF/SPF"
        )

    raw_type = cfg.text(
        "netlist",
        "pin_order_type",
        default="CDL Netlist File",
    )
    pin_type = raw_type.strip().casefold()
    if pin_type in {"", "cdl netlist file", "source"}:
        pin_order_file = ctx.source_path
        if not pin_order_file:
            raise ValueError(
                "StarRC User Pin Order cannot use the CDL netlist source for "
                f"input type {ctx.input_type}; select a User Defined File"
            )
        return pin_order_file

    if pin_type in {"user defined file", "file"}:
        raw_file = cfg.text(
            "netlist", "pin_order_file"
        ).strip()
        if not raw_file:
            raise ValueError(
                "StarRC User Pin Order is enabled but no pin-order file was provided"
            )
        pin_order_file = cfg.resolve_path(raw_file)
        return str(require_starrc_file(pin_order_file, "pin-order file"))

    raise ValueError(f"Unsupported StarRC pin-order type: {raw_type}")


def starrc_parasitic_info_lines(
    cfg: RceConfig,
    netlist_format: str,
) -> tuple[bool, list[str], dict[str, str]]:
    coordinates, res_layer, res_dimensions = cfg.parasitic_info_flags()
    if netlist_format == "OA" and any((coordinates, res_layer, res_dimensions)):
        raise ValueError(
            "StarRC OA view output stores parasitic geometry natively; disable "
            "the ASCII parasitic information options"
        )
    if coordinates and netlist_format != "SPF":
        raise ValueError(
            "StarRC R&C coordinates require CAPACITOR_TAIL_COMMENTS, which "
            "is supported only for DSPF/SPF output; select DSPF/SPF or "
            "disable netlist.parasitic_coordinates"
        )

    if not any((coordinates, res_layer, res_dimensions)):
        return False, [], {}

    lines = ["NETLIST_TAIL_COMMENTS: YES"]
    requirements = {
        "REDUCTION": "NO",
        "POWER_REDUCTION": "NO",
        "NETLIST_TAIL_COMMENTS": "YES",
    }
    if coordinates:
        lines.extend(
            [
                "NETLIST_CONNECT_SECTION: YES",
                "NETLIST_NODE_SECTION: YES",
                "EXTRA_GEOMETRY_INFO: NODE RES",
                "KEEP_VIA_NODES: YES",
                "CAPACITOR_TAIL_COMMENTS: YES",
                "NETLIST_UNSCALED_COORDINATES: YES",
            ]
        )
        requirements.update(
            {
                "NETLIST_CONNECT_SECTION": "YES",
                "NETLIST_NODE_SECTION": "YES",
                "EXTRA_GEOMETRY_INFO": "NODE RES",
                "KEEP_VIA_NODES": "YES",
                "CAPACITOR_TAIL_COMMENTS": "YES",
                "NETLIST_UNSCALED_COORDINATES": "YES",
            }
        )
    if coordinates or res_dimensions:
        lines.append("NETLIST_UNSCALED_RES_PROP: YES")
        requirements["NETLIST_UNSCALED_RES_PROP"] = "YES"
    return True, lines, requirements


def starrc_selection_lines(
    cfg: RceConfig,
    ctx: DesignContext,
    *,
    net_type: str,
    cell_type: str,
) -> tuple[list[str], dict[str, str]]:
    lines: list[str] = []
    requirements: dict[str, str] = {}

    mode, nets = cfg.net_selection()
    if mode is not None:
        invalid = [name for name in nets if name.startswith("!")]
        if invalid:
            raise ValueError(
                "StarRC net names must not start with '!'; choose Include Nets "
                "or Exclude Nets in selection.net_type"
            )
        selected = nets if mode == "include" else ["*", *(f"!{net}" for net in nets)]
        nets_file = ctx.log_dir / "star.nets"
        write_text(nets_file, f"NETS: {' '.join(selected)}\n")
        lines.append(f"NETS_FILE: {nets_file}")
        requirements["NET_TYPE"] = net_type

    cells = cfg.blocked_cells()
    if cells:
        cells_file = ctx.log_dir / "star.cells"
        write_text(cells_file, f"SKIP_CELLS: {' '.join(cells)}\n")
        lines.append(f"SKIP_CELLS_FILE: {cells_file}")
        requirements["CELL_TYPE"] = cell_type

    return lines, requirements


def starrc_filter_lines(cfg: RceConfig) -> tuple[list[str], dict[str, str]]:
    lines: list[str] = []
    requirements: dict[str, str] = {}
    cap_abs = cfg.text("filter", "cap_value")
    cap_rel = cfg.text(
        "filter", "cap_percentage"
    )

    absolute = (
        format_decimal(
            femtofarads_to_farads(cap_abs, "StarRC absolute capacitance filter")
        )
        if cap_abs
        else ""
    )
    relative = (
        format_decimal(
            percent_to_ratio(cap_rel, "StarRC relative capacitance filter")
        )
        if cap_rel
        else ""
    )
    if absolute or relative:
        operation = "AND" if absolute and relative else "OR"
        absolute = absolute or "0"
        relative = relative or "0"
        requirements.update(
            {
                "COUPLING_ABS_THRESHOLD": absolute,
                "COUPLING_REL_THRESHOLD": relative,
                "COUPLING_THRESHOLD_OPERATION": operation,
            }
        )
        lines.extend(f"{name}: {value}" for name, value in requirements.items())

    res_value = cfg.text("filter", "res_value")
    if res_value:
        resistance = format_decimal(
            nonnegative_decimal(res_value, "StarRC resistance filter")
        )
        requirements.update(
            {
                "NETLIST_MINRES_HANDLING": "SHORT",
                "NETLIST_MINRES_THRESHOLD": resistance,
            }
        )
        lines.extend(
            [
                "NETLIST_MINRES_HANDLING: SHORT",
                f"NETLIST_MINRES_THRESHOLD: {resistance}",
            ]
        )
    return lines, requirements


STARRC_NETLIST_FORMATS = {
    "dspf": "SPF",
    "spf": "SPF",
    "spef": "SPEF",
}


STARRC_HIERARCHICAL_SEPARATORS = {"/", ".", "|", ":"}
