"""Render Calibre PEX extraction and netlist options."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .config import DesignContext, RceConfig
from .filter_units import format_decimal, nonnegative_decimal, percent_to_ratio
from .netlist_postprocess import configured_bus_delimiter_mapping
from .pex_engine import PexMode, normalize_rc_type, require_calibre_pex_engine
from .textutil import q
from .xrc_models import XrcSettings
from .xrc_paths import require_xrc_file


def xrc_netlist_options(
    cfg: RceConfig, ctx: DesignContext, settings: XrcSettings
) -> list[str]:
    engine = require_calibre_pex_engine(cfg.ext_tool)
    name_option = "SOURCENAMES" if ctx.name_source == "schematic" else "LAYOUTNAMES"
    options = [settings.netlist_format, name_option]
    if (
        not settings.simple_netlist
        and settings.netlist_format in {"DSPF", "SPEF"}
        and settings.bus_delimiter
    ):
        options.extend(["BUSDELIM", f'"{settings.bus_delimiter}"'])
    hierarchy_enabled = cfg.flag(
        "netlist", "hierarchy_delimiter_enable"
    )
    if settings.netlist_format == "CALIBREVIEW" and (
        hierarchy_enabled
        or cfg.flag("netlist", "brackets_replace")
    ):
        raise ValueError(
            "Calibre View controls OA names natively; disable bracket replacement "
            "and hierarchy delimiter customization"
        )
    if hierarchy_enabled:
        separator = cfg.text(
            "netlist", "hierarchy_delimiter"
        )
        if separator:
            options.extend(["SEPARATOR", f'"{q(separator)}"'])

    coordinates, res_layer, res_dimensions = cfg.parasitic_info_flags()
    if settings.simple_netlist and (coordinates or res_layer or res_dimensions):
        raise ValueError(
            f"{engine.label} NONE/noRC does not support parasitic netlist "
            "information; disable coordinates, resistor layer, and resistor "
            "dimensions"
        )
    if coordinates and settings.capacitance_only:
        raise ValueError(
            f"{engine.label} CLOCATION does not report capacitor locations during "
            "capacitance-only (-c) extraction; select an RC extraction type or "
            "disable netlist.parasitic_coordinates"
        )
    parasitic_options = (
        ("CLOCATION", coordinates),
        ("LOCATION", coordinates),
        ("RLAYER", res_layer),
        ("RLENGTH", res_dimensions),
        ("RLOCATION", coordinates),
        ("RWIDTH", res_dimensions),
    )
    options.extend(option for option, enabled in parasitic_options if enabled)
    remove_instances = cfg.text(
        "netlist", "dspf_remove_instances"
    ) == "TRUE"
    if (
        settings.simple_netlist
        and settings.netlist_format == "DSPF"
        and remove_instances
    ):
        raise ValueError(
            f"{engine.label} NONE/noRC does not support removing the DSPF "
            "instance section"
        )
    if (
        settings.netlist_format == "DSPF"
        and remove_instances
    ):
        options.append("NOINSTANCESECTION")
    return options


def xrc_option_lines(
    cfg: RceConfig,
    ctx: DesignContext,
    temperature_override: str | None = None,
    *,
    simple_netlist: bool = False,
) -> list[str]:
    engine = require_calibre_pex_engine(cfg.ext_tool)
    lines: list[str] = []
    cap_abs = cfg.text("filter", "cap_value")
    cap_percent = cfg.text("filter", "cap_percentage")
    res_value = cfg.text("filter", "res_value")
    net_selection = cfg.flag("selection", "net_enable")
    if simple_netlist:
        if (
            cap_abs
            or cap_percent
            or res_value
            or net_selection
        ):
            raise ValueError(
                f"{engine.label} NONE/noRC does not support parasitic filters "
                "or net selection"
            )
        return lines

    temperature = (
        temperature_override
        if temperature_override is not None
        else cfg.text("extract", "temperature")
    )
    if temperature:
        _decimal(temperature, f"{engine.label} temperature", allow_negative=True)
        lines.append(f"PEX EXTRACT TEMPERATURE {temperature}")

    cap_options: list[str] = []
    if cap_abs:
        absolute = nonnegative_decimal(cap_abs, f"{engine.label} capacitance filter")
        cap_options.extend(["ABSOLUTE", format_decimal(absolute)])
    if cap_percent:
        ratio = percent_to_ratio(
            cap_percent, f"{engine.label} relative capacitance filter"
        )
        if cap_options:
            cap_options.append("AND")
        cap_options.extend(["RATIO", format_decimal(ratio)])
    if cap_options:
        lines.append("PEX REDUCE CC " + " ".join(cap_options))

    if res_value:
        resistance = nonnegative_decimal(
            res_value, f"{engine.label} resistance filter"
        )
        lines.append(f"PEX REDUCE MINRES SHORT {format_decimal(resistance)}")

    if cfg.flag("selection", "net_enable"):
        mode, nets = cfg.net_selection()
        if mode == "include":
            keyword = "INCLUDE"
        elif mode == "exclude":
            keyword = "EXCLUDE"
        else:
            raise AssertionError(
                f"Enabled {engine.label} net selection has no mode"
            )
        quoted = " ".join(f'"{q(net)}"' for net in nets)
        name_option = (
            "SOURCENAMES" if ctx.name_source == "schematic" else "LAYOUTNAMES"
        )
        lines.append(f"PEX EXTRACT {keyword} {name_option} {quoted}")
    return lines


def xrc_floating_net_line(cfg: RceConfig) -> str:
    """Return the SVRF statement for the GUI floating-net choice."""
    keyword = (
        "GROUNDED"
        if cfg.flag("extract", "remove_floating_nets")
        else "ALL"
    )
    return f"PEX EXTRACT FLOATING NETS {keyword}"


def selection_switch(cfg: RceConfig, model: PexMode) -> str | None:
    """Return the PDB net-selection switch for an Include-net extraction."""
    selection, _ = cfg.net_selection()
    if selection != "include":
        return None
    return require_calibre_pex_engine(cfg.ext_tool).selection_argument(model)


def xrc_mode(cfg: RceConfig) -> PexMode:
    """Return the Calibre PEX parasitic model selected in the request."""
    rc_type = normalize_rc_type(
        cfg.text("extract", "rc_type", default="R+Cg+Cc")
    )
    return require_calibre_pex_engine(cfg.ext_tool).mode(rc_type)


def pin_order_statement(cfg: RceConfig) -> str | None:
    engine = require_calibre_pex_engine(cfg.ext_tool)
    if not cfg.flag("netlist", "pin_order_enable"):
        return None

    raw_type = cfg.text(
        "netlist", "pin_order_type",
        default="CDL Netlist File",
    )
    pin_type = raw_type.strip().casefold()
    if pin_type in {"", "cdl netlist file", "source"}:
        return "PEX PIN ORDER SOURCE"
    if pin_type == "layout":
        return "PEX PIN ORDER LAYOUT"
    if pin_type in {"user defined file", "file"}:
        raw_file = cfg.text("netlist", "pin_order_file")
        if not raw_file:
            raise ValueError(
                f"{engine.label} pin-order file is enabled but no file was provided"
            )
        pin_file = cfg.resolve_path(raw_file)
        require_xrc_file(pin_file, f"{engine.label} pin-order file")
        return f'PEX PIN ORDER FILE "{q(str(pin_file))}"'
    raise ValueError(
        f"Unsupported {engine.label} pin-order type: {raw_type}"
    )


def bracket_replacement(cfg: RceConfig) -> tuple[str | None, str | None]:
    mapping = configured_bus_delimiter_mapping(cfg)
    if mapping is None:
        return None, None
    return (
        f'PEX NETLIST CHARACTER MAP "{mapping.calibre_character_map}"',
        mapping.target,
    )


def _decimal(value: str, label: str, *, allow_negative: bool = False) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid {label}: {value}") from exc
    if not number.is_finite() or (number < 0 and not allow_negative):
        raise ValueError(f"Invalid {label}: {value}")
    return number
