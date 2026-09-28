"""Display formatting and utilization thresholds for monitor cells."""

from __future__ import annotations

PROGRESS_NORMAL = "normal"
PROGRESS_WARNING = "warning"
PROGRESS_CRITICAL = "critical"

def display_integer(value: int | None) -> str:
    return "-" if value is None else f"{value:,}"


def display_float(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def display_percent(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


def display_bytes(value: int | None) -> str:
    if value is None:
        return "-"
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    amount = float(value)
    unit = units[0]
    for candidate in units:
        unit = candidate
        if amount < 1024 or candidate == units[-1]:
            break
        amount /= 1024
    return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"


def display_duration(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    days, remainder = divmod(seconds, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, remaining_seconds = divmod(remainder, 60)
    clock = f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}"
    return f"{days}d {clock}" if days else clock


def utilization_level(ratio: float | None) -> str:
    if ratio is not None and ratio > 0.8:
        return PROGRESS_CRITICAL
    if ratio is not None and ratio >= 0.5:
        return PROGRESS_WARNING
    return PROGRESS_NORMAL
