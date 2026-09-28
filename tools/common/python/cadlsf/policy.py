"""Deterministic host recommendation policy with explainable ordering."""

from __future__ import annotations

from dataclasses import dataclass
from math import inf

from .model import HostInfo


@dataclass(frozen=True)
class HostRecommendation:
    host: HostInfo
    reasons: tuple[str, ...]


def _ascending_unknown_last(value: float | int | None) -> float:
    return inf if value is None else float(value)


def _descending_unknown_last(value: float | int | None) -> float:
    return inf if value is None else -float(value)


def host_sort_key(host: HostInfo) -> tuple[object, ...]:
    per_slot_load = None
    if host.load_1m is not None and host.max_slots and host.max_slots > 0:
        per_slot_load = host.load_1m / host.max_slots
    return (
        not host.is_available,
        _descending_unknown_last(host.free_slots),
        _ascending_unknown_last(host.slot_utilization),
        _ascending_unknown_last(per_slot_load),
        _ascending_unknown_last(host.cpu_utilization),
        _descending_unknown_last(host.memory_available_bytes),
        host.name.casefold(),
        host.name,
    )


def recommendation_reasons(host: HostInfo) -> tuple[str, ...]:
    reasons: list[str] = []
    if host.free_slots is not None:
        reasons.append(f"{host.free_slots} free slots")
    if host.slot_utilization is not None:
        reasons.append(f"{host.slot_utilization:.0%} slot utilization")
    if host.cpu_utilization is not None:
        reasons.append(f"{host.cpu_utilization:.0%} CPU utilization")
    if host.memory_available_bytes is not None:
        gibibytes = host.memory_available_bytes / (1024**3)
        reasons.append(f"{gibibytes:.1f} GiB memory available")
    return tuple(reasons) or ("available host; load metrics unavailable",)


def recommend_hosts(hosts: tuple[HostInfo, ...]) -> tuple[HostRecommendation, ...]:
    available = (host for host in hosts if host.is_available)
    return tuple(
        HostRecommendation(host, recommendation_reasons(host))
        for host in sorted(available, key=host_sort_key)
    )
