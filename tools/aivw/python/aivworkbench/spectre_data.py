"""Spectre model dependency and finite, unit-bearing waveform boundaries."""

from __future__ import annotations

from bisect import bisect_left
import csv
import math
from pathlib import Path
import re

from .workspace import sha256_file


def stage_models(source: Path, main: str, destination: Path) -> list[dict]:
    """Copy a bounded literal include graph; sections may include the same file."""
    source = source.resolve(strict=True)
    destination = Path(destination)
    if destination.exists() and destination.is_symlink():
        raise ValueError("model staging destination is a symlink")
    destination.mkdir(parents=True, exist_ok=True)
    pending = [main]
    records = {}
    total = 0
    while pending:
        relative = pending.pop()
        if relative in records:
            continue
        if len(records) >= 64:
            raise ValueError("model include graph exceeds 64 files")
        path = Path(relative)
        if path.is_absolute() or any(p in {"..", "."} for p in path.parts) or "\\" in relative:
            raise ValueError("model include escapes model root")
        current = source
        for part in path.parts:
            current /= part
            if current.is_symlink():
                raise ValueError("model include traverses a symlink")
        if not current.is_file() or not current.resolve().is_relative_to(source):
            raise ValueError("model include is missing or outside source root")
        size = current.stat().st_size
        total += size
        if size > 32 * 1024 * 1024 or total > 128 * 1024 * 1024:
            raise ValueError("model include graph exceeds byte budget")
        data = current.read_bytes()
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        current_target = destination
        for part in path.parts:
            current_target /= part
            if current_target.is_symlink():
                raise ValueError("model staging traverses a symlink")
        with target.open("xb") as stream:
            stream.write(data)
        digest = sha256_file(target)
        if sha256_file(current) != digest:
            raise ValueError("model changed while staging")
        records[relative] = {"source": str(current), "relative_path": relative,
                             "sha256": digest, "size": size,
                             "staged_sha256": digest, "staged_size": size}
        for line in data.decode("utf-8").splitlines():
            line = line.split("//", 1)[0].strip()
            if not re.match(r"include\b", line, re.I):
                continue
            match = re.fullmatch(r'include\s+"([A-Za-z0-9_./-]+)"(?:\s+section\s*=\s*[A-Za-z0-9_]+)?\s*', line, re.I)
            if not match:
                raise ValueError("unsupported nonliteral model include")
            pending.append((path.parent / match[1]).as_posix())
    return [records[k] for k in sorted(records)]


def verify_model_sources(records: list[dict], staged_root: Path | None = None) -> None:
    for record in records:
        path = Path(record["source"])
        if (path.is_symlink() or path.stat().st_size != record["size"]
                or sha256_file(path) != record["sha256"]):
            raise ValueError("model source changed during simulation")
        if staged_root is not None:
            staged = Path(staged_root) / record["relative_path"]
            if (staged.is_symlink() or not staged.is_file()
                    or staged.stat().st_size != record["staged_size"]
                    or sha256_file(staged) != record["staged_sha256"]):
                raise ValueError("staged model changed during simulation")


def validate_psf_units(path: Path, signals: dict[str, str]) -> None:
    with path.open() as stream:
        header = []
        for line in stream:
            if line.strip() == "VALUE":
                break
            header.append(line)
            if sum(map(len, header)) > 128 * 1024:
                raise ValueError("PSF header exceeds unit-validation bound")
    text = "".join(header)
    if '"analysis type" "tran"' not in text or '\nSWEEP\n' not in text or '\nTRACE\n' not in text:
        raise ValueError("missing transient PSF metadata")
    types, remainder = text.split('\nSWEEP\n', 1)
    sweep, traces = remainder.split('\nTRACE\n', 1)
    if not re.search(r'"time" "sweep" PROP\([^)]*"units" "s"', sweep):
        raise ValueError("PSF time unit must be seconds")
    units = dict(re.findall(r'"([^"\n]+)" FLOAT DOUBLE PROP\(\s*"units" "([^"\n]*)"', types))
    records = re.findall(r'^"([^"\n]+)" "([^"\n]+)"(?: PROP\(\s*"units" "([^"\n]*)"\s*\))?\s*$', traces, re.M)
    actual = {name: override or units.get(kind) for name, kind, override in records}
    if actual != signals or len(actual) != len(records):
        raise ValueError("PSF signal units differ from measurement contract")


def measure_csv(path: Path, signals: dict[str, str], sample_times: list[float],
                stop_s: float) -> dict:
    """Require ordered finite samples with complete coverage before interpolation."""
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("waveform CSV exceeds byte budget")
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream)
        if next(reader, None) != ["time[s]", *[f"{n}[{u}]" for n, u in signals.items()]]:
            raise ValueError("waveform columns or units disagree with contract")
        rows = []
        for row in reader:
            if len(rows) >= 250000 or len(row) != len(signals) + 1:
                raise ValueError("waveform sample count or column count invalid")
            values = [float(v) for v in row]
            if not all(math.isfinite(v) for v in values):
                raise ValueError("non-finite waveform evidence")
            if rows and values[0] <= rows[-1][0]:
                raise ValueError("waveform time must be strictly increasing")
            rows.append(values)
    if len(rows) < 2 or rows[0][0] != 0 or abs(rows[-1][0] - stop_s) > max(1e-15, stop_s * 1e-12):
        raise ValueError("waveform does not cover the full experiment interval")
    # Spectre can serialize the requested endpoint a few floating-point ULPs
    # below stop_s. Coverage was already checked; normalize that endpoint in
    # memory so a sample at stop_s does not incorrectly request extrapolation.
    # Leave covering waveforms unchanged to preserve historical interpolation.
    if rows[-1][0] < stop_s:
        rows[-1][0] = stop_s
    times = [r[0] for r in rows]
    samples = []
    for t in sample_times:
        if isinstance(t, bool) or not math.isfinite(t) or not times[0] <= t <= times[-1]:
            raise ValueError("measurement time is outside waveform; extrapolation forbidden")
        hi = bisect_left(times, t)
        if times[hi] == t:
            values = rows[hi][1:]
        else:
            lo = hi - 1
            fraction = (t - times[lo]) / (times[hi] - times[lo])
            values = [a + fraction * (b - a) for a, b in zip(rows[lo][1:], rows[hi][1:])]
        samples.append({"time_s": t, "values": dict(zip(signals, values))})
    return {"schema_version": 1, "kind": "spectre-characterization-measurements",
            "sample_count": len(rows), "units": signals, "samples": samples,
            "extrema": {name: {"min": min(r[i] for r in rows), "max": max(r[i] for r in rows)}
                        for i, name in enumerate(signals, 1)},
            "behavior_verdict": "NOT_ESTABLISHED", "correlation_status": "BLOCKED_CONTRACT"}
