"""Canonical GUI-unit conversion for parasitic filter thresholds."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def nonnegative_decimal(
    value: str,
    label: str,
    *,
    maximum: Decimal | None = None,
) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid {label}: {value}") from exc
    if not number.is_finite() or number < 0 or (
        maximum is not None and number > maximum
    ):
        limit = f" between 0 and {format_decimal(maximum)}" if maximum else ""
        raise ValueError(f"Invalid {label}{limit}: {value}")
    return number


def percent_to_ratio(value: str, label: str) -> Decimal:
    percent = nonnegative_decimal(value, label, maximum=Decimal("100"))
    return percent / Decimal("100")


def femtofarads_to_farads(value: str, label: str) -> Decimal:
    femtofarads = nonnegative_decimal(value, label)
    return femtofarads * Decimal("1e-15")


def format_decimal(value: Decimal) -> str:
    rendered = format(value, "f").rstrip("0").rstrip(".")
    return rendered or "0"
