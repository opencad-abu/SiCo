"""Render Quantus pin order, parasitic information and filter options."""

from __future__ import annotations

from decimal import Decimal

from .config import DesignContext, RceConfig
from .filter_units import format_decimal, nonnegative_decimal, percent_to_ratio


def qrc_pin_order_file(cfg: RceConfig, ctx: DesignContext) -> str:
    raw_type = cfg.text(
        "netlist",
        "pin_order_type",
        default="CDL Netlist File",
    )
    pin_type = raw_type.strip().casefold()
    if pin_type in {"", "cdl netlist file", "source"}:
        if not ctx.source_path:
            raise ValueError(
                "QRC User Pin Order cannot use the CDL netlist source for "
                f"input type {ctx.input_type}; select a User Defined File"
            )
        return ctx.source_path
    if pin_type in {"user defined file", "file"}:
        raw_file = cfg.text(
            "netlist", "pin_order_file"
        ).strip()
        if not raw_file:
            raise ValueError(
                "QRC User Pin Order is enabled but no pin-order file was provided"
            )
        pin_file = cfg.resolve_path(raw_file)
        if not pin_file.is_file():
            raise FileNotFoundError(f"Cannot access QRC pin-order file: {pin_file}")
        return str(pin_file)
    raise ValueError(f"Unsupported QRC pin-order type: {raw_type}")


def qrc_parasitic_info_options(
    cfg: RceConfig, output_type: str
) -> list[str]:
    enabled = dict(zip(QRC_PARASITIC_INFO_SUPPORT, cfg.parasitic_info_flags()))
    output_format = output_type.strip().casefold()
    unsupported = [
        (name, formats)
        for name, formats in QRC_PARASITIC_INFO_SUPPORT.items()
        if enabled[name] and output_format not in formats
    ]
    if unsupported:
        details = "; ".join(
            f"{name} supports {', '.join(sorted(formats))}"
            for name, formats in unsupported
        )
        raise ValueError(
            f"QRC output format {output_type!r} does not support the selected "
            f"More Netlist Options: {details}"
        )

    options: list[str] = []
    if enabled["R&C Coordinates"]:
        options.append("-output_xy parasitic_res parasitic_cap")
    if enabled["Parasitic R Layer Name"]:
        options.append("-include_parasitic_res_model_by_sub_conductor true")
    if enabled["Parasitic R Size (W&L)"]:
        options.extend(
            [
                "-include_parasitic_res_length true",
                "-include_parasitic_res_width_drawn true",
            ]
        )
    return options


def qrc_filter_cap(cfg: RceConfig) -> str:
    cap_value = cfg.text("filter", "cap_value")
    cap_percentage = cfg.text("filter", "cap_percentage")
    absolute = (
        format_decimal(
            nonnegative_decimal(
                cap_value,
                "QRC absolute capacitance filter",
                maximum=Decimal("100"),
            )
        )
        if cap_value
        else ""
    )
    relative = (
        format_decimal(
            percent_to_ratio(cap_percentage, "QRC relative capacitance filter")
        )
        if cap_percentage
        else ""
    )
    if absolute and relative:
        return (
            "filter_coupling_cap "
            f"-coupling_cap_threshold_absolute {absolute} "
            f"-coupling_cap_threshold_relative {relative}"
        )
    if absolute:
        return f"filter_coupling_cap -coupling_cap_threshold_absolute {absolute}"
    if relative:
        return f"filter_coupling_cap -coupling_cap_threshold_relative {relative}"
    return ""


def qrc_filter_res(cfg: RceConfig) -> str:
    value = cfg.text("filter", "res_value")
    if not value:
        return ""
    resistance = nonnegative_decimal(value, "QRC resistance filter")
    return f"-min_res {format_decimal(resistance)}"


QRC_PARASITIC_INFO_SUPPORT = {
    "R&C Coordinates": {"dspf", "sp", "spef", "smartview"},
    "Parasitic R Layer Name": {"dspf", "sp", "spef", "extview", "smartview"},
    "Parasitic R Size (W&L)": {"dspf", "sp", "spef", "extview", "smartview"},
}
