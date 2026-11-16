"""Resolve a dummy-fill profile from the active project and PDK.

Users select metal layers in the form; they never edit a profile.  The profile
is found from a project site file, an explicit environment variable, or by
deriving the deck switches and result names from the PDK dummy-fill deck.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tarfile
from typing import Any, Iterable

from .profile import FillProfile, load_profile
from .rules import RuleFillSpec


PROFILE_SCHEMA = "sico.dmyfill.profile.v1"

_FILL_DEFINE = re.compile(r"^\s*#DEFINE\s+FILL_DM([0-9]+)\b")
_IND_FILL_DEFINE = re.compile(r"^\s*#DEFINE\s+FILL_indDM([0-9]+)\b")
_THICKNESS = re.compile(r"^\s*#DEFINE\s+([0-9A-Z_]+_THICK_M([0-9]+))\b")
_CHECK_MAP = re.compile(
    r"^\s*DRC\s+CHECK\s+MAP\s+(\S+)\s+([0-9]+)\s+([0-9]+)\b"
)
_ACTIVE_DEFINE = re.compile(r"^\s*#DEFINE\s+([A-Za-z0-9_]+)\b")
_DEFINE_ANY = re.compile(r"^\s*(?://\s*)?#DEFINE\s+([A-Za-z0-9_]+)\b")
_DM2CHIP = re.compile(r"^\s*VARIABLE\s+dm2Chip\s+([0-9.]+)\b")
_RULE = re.compile(r"^(indDM|DM|RM)([0-9]+)_FILL$")


class DiscoveryError(ValueError):
    """No usable dummy-fill profile could be resolved."""


@dataclass(frozen=True)
class Discovery:
    """A resolved profile and where it came from."""

    profile: FillProfile | None
    source: str
    deck_source: Path | None = None
    rule: RuleFillSpec | None = None
    note: str = ""
    warnings: tuple[str, ...] = ()


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _absolute(value: str | Path, base: Path | None = None) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() and base is not None:
        path = base / path
    return path.resolve()


def _cache_root() -> Path:
    explicit = _env("SICO_DMYFILL_CACHE_DIR")
    if explicit:
        return _absolute(explicit)
    temp = _env("SICO_TEMP_DIR")
    if temp:
        return Path(temp).expanduser().resolve() / "dmyfill"
    xdg = _env("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg).expanduser().resolve() / "sico/dmyfill"
    return Path.home() / ".cache/sico/dmyfill"


def _safe_extract(archive: tarfile.TarFile, destination: Path) -> None:
    for member in archive.getmembers():
        relative = Path(member.name)
        if relative.is_absolute() or ".." in relative.parts:
            raise DiscoveryError(f"unsafe archive member: {member.name}")
        if not (member.isfile() or member.isdir()):
            continue
        archive.extract(member, destination)


def _deck_in_archive(archive_path: Path) -> Path:
    digest = hashlib.sha256(
        f"{archive_path}:{archive_path.stat().st_size}:{archive_path.stat().st_mtime_ns}".encode()
    ).hexdigest()[:16]
    destination = _cache_root() / "decks" / digest
    marker = destination / ".complete"
    if marker.is_file():
        return _pick_deck_file(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as archive:
        _safe_extract(archive, destination)
    marker.write_text("ok\n", encoding="ascii")
    return _pick_deck_file(destination)


def _pick_deck_file(directory: Path) -> Path:
    candidates = [
        path
        for path in directory.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".encrypt", ".cal", ".drc", ".txt", ".rul"}
    ]
    if not candidates:
        raise DiscoveryError(f"archive contains no fill deck: {directory}")

    def score(path: Path) -> tuple[int, int, str]:
        name = path.name.lower()
        metal = 1 if "dummy_metal" in name or "dummy.metal" in name else 0
        ye = 1 if "_ye" in name else 0
        return (-metal, ye, name)

    return sorted(candidates, key=score)[0]


def _deck_file(source: Path) -> Path:
    suffixes = "".join(source.suffixes).lower()
    if suffixes.endswith(".tar.gz") or suffixes.endswith(".tgz"):
        return _deck_in_archive(source)
    return source


def _layer_map_index(
    layer_map: Path | None,
) -> tuple[dict[int, str], set[str]]:
    """Map layer numbers to OA layer names and collect drawing layers."""
    by_number: dict[int, str] = {}
    drawing: set[str] = set()
    if layer_map is None or not layer_map.is_file():
        return by_number, drawing
    for line in layer_map.read_text(encoding="utf-8", errors="replace").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        parts = text.split()
        if len(parts) < 2 or parts[1] != "drawing":
            continue
        layer = parts[0]
        drawing.add(layer)
        if len(parts) >= 3 and parts[2].isdigit():
            by_number.setdefault(int(parts[2]), layer)
    return by_number, drawing


def _active_defines(text: str) -> set[str]:
    result: set[str] = set()
    for line in text.splitlines():
        match = _ACTIVE_DEFINE.match(line)
        if match:
            result.add(match.group(1))
    return result


def _all_defines(text: str) -> set[str]:
    result: set[str] = set()
    for line in text.splitlines():
        match = _DEFINE_ANY.match(line)
        if match:
            result.add(match.group(1))
    return result


def _fill_define_for(
    defines: set[str],
    number: int,
    layer_name: str,
) -> str | None:
    """Find a per-layer FILL switch without assuming one vendor spelling."""
    match = re.search(r"([0-9]+)$", layer_name)
    index = int(match.group(1)) if match is not None else None
    for name in sorted(defines):
        upper = name.upper()
        if "FILL" not in upper or "IND" in upper:
            continue
        if index is not None and re.search(
            rf"(?:DM|M|METAL)_?{index}\b", upper
        ):
            return name
        if layer_name.upper() in upper:
            return name
        if re.search(rf"(?:^|_){number}(?:_|$)", upper):
            return name
        if index is not None and re.search(
            rf"(?:^|_){index}(?:_|$)", upper
        ):
            return name
    return None


def _thickness_by_layer(defines: set[str]) -> dict[int, str]:
    result: dict[int, str] = {}
    for name in defines:
        upper = name.upper()
        if "THICK" not in upper:
            continue
        match = re.search(r"M([0-9]+)$", upper)
        if match is None:
            match = re.search(r"THICK[A-Z0-9_]*?([0-9]+)$", upper)
        if match is not None:
            result[int(match.group(1))] = name
    return result


def _metal_layer_name(layer: str) -> bool:
    return (
        re.fullmatch(r"(?:M|MET|METAL|MT)[0-9]+", layer.upper())
        is not None
    )


def profile_data_from_deck(
    deck: Path,
    layer_map: Path | None = None,
) -> dict[str, Any]:
    """Derive a profile document from a foundry fill deck.

    Layer identity comes from DRC CHECK MAP layer numbers and TECH_LAYER_MAP,
    not from a fixed DM<n>/M<n> result-name spelling.
    """
    text = deck.read_text(encoding="utf-8", errors="replace")
    defines = _all_defines(text)
    active_defines = _active_defines(text)
    check_maps: list[tuple[str, int, int]] = []
    region_margin = 0.0
    for line in text.splitlines():
        match = _CHECK_MAP.match(line)
        if match:
            check_maps.append(
                (match.group(1), int(match.group(2)), int(match.group(3)))
            )
            continue
        match = _DM2CHIP.match(line)
        if match:
            region_margin = float(match.group(1))

    if (
        "FILL_DOD_DPO" in defines
        or any(name in ("GEN_DOD", "GEN_DPO") for name, _, _ in check_maps)
    ):
        raise DiscoveryError(
            f"{deck} is an OD/PO dummy deck (FILL_DOD_DPO), "
            "not a metal dummy deck; set DMYFILL_FILE to the Dummy_Metal deck"
        )

    layer_by_number, drawing = _layer_map_index(layer_map)
    thickness_by_number = _thickness_by_layer(active_defines)
    grouped: dict[str, tuple[int, dict[str, str]]] = {}
    for result, number, datatype in check_maps:
        if "FILL" not in result.upper():
            continue
        layer = layer_by_number.get(number)
        if layer is None and layer_map is None:
            layer = f"M{number}"
        if layer is None:
            continue
        if not _metal_layer_name(layer):
            continue
        if drawing and layer not in drawing:
            continue
        purpose = (
            "odummy"
            if result.upper().startswith("R")
            or "_R" in result.upper()
            or datatype == 7
            else "dummy"
        )
        if layer not in grouped:
            grouped[layer] = (number, {})
        grouped[layer][1][result] = purpose

    if not grouped:
        raise DiscoveryError(
            f"no metal FILL rules matched TECH_LAYER_MAP in deck: {deck}"
        )

    layers: dict[str, Any] = {}
    rules: dict[str, Any] = {}
    for layer, (number, layer_rules) in sorted(
        grouped.items(), key=lambda item: item[1][0]
    ):
        fill_define = _fill_define_for(active_defines, number, layer)
        if fill_define is None:
            continue
        match = re.search(r"([0-9]+)$", layer)
        layer_index = int(match.group(1)) if match is not None else None
        filtered = {
            result: purpose
            for result, purpose in layer_rules.items()
            if not (
                layer_index in thickness_by_number
                and result.upper().startswith("RM")
            )
        }
        if not filtered:
            continue
        entry: dict[str, Any] = {"fill_define": fill_define}
        thickness = (
            thickness_by_number.get(layer_index)
            if layer_index is not None
            else None
        )
        if thickness is not None:
            entry["thickness_define"] = thickness
        layers[layer] = entry
        for result, purpose in filtered.items():
            rules[result] = {"layer": layer, "purpose": purpose}

    if not layers:
        raise DiscoveryError(
            f"no controllable metal FILL layers in deck: {deck}; "
            "no active FILL switch matched the layer number"
        )
    return {
        "schema": PROFILE_SCHEMA,
        "deck": str(deck),
        "layer_map": str(layer_map) if layer_map is not None else None,
        "layers": layers,
        "rules": rules,
        "options": {
            "dm_on_corner": True,
            "with_sealring": "WithSealring" in active_defines,
            "region_margin": region_margin,
        },
    }


def _write_profile(document: dict[str, Any], source: Path) -> Path:
    digest = hashlib.sha256(
        json.dumps(document, sort_keys=True, ensure_ascii=True).encode()
    ).hexdigest()[:16]
    target = _cache_root() / "profiles" / f"{source.stem}-{digest}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        temporary = target.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(document, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    return target


def profile_from_deck(
    deck: Path,
    layer_map: Path | None = None,
) -> FillProfile:
    """Create and cache a profile from a fill deck."""
    source = deck.resolve()
    materialized = _deck_file(source)
    document = profile_data_from_deck(materialized, layer_map)
    generated = _write_profile(document, materialized)
    return load_profile(generated)


def _layer_map_from_environment() -> Path | None:
    value = _env("TECH_LAYER_MAP")
    if value:
        path = Path(value).expanduser()
        if path.is_file():
            return path
    return None


def _rule_path(value: str) -> Path:
    """Accept DMYFILL_FILE in the same "name,path" form as DRC_FILE."""
    text = value.strip()
    if "," in text:
        text = text.split(",", 1)[1].strip()
    return _absolute(text)


def _pdk_deck_candidates() -> Iterable[Path]:
    pdk = _env("PROJ_PDK_HOME")
    if not pdk:
        return
    root = Path(pdk).expanduser().resolve()
    for name in ("Calibre/dummy_util", "calibre/dummy_util", "dummy_util"):
        directory = root / name
        if not directory.is_dir():
            continue
        candidates = [
            path
            for path in directory.iterdir()
            if path.is_file()
            and (
                path.name.lower().endswith(".tar.gz")
                or path.suffix.lower() in {".encrypt", ".cal", ".drc"}
            )
        ]

        def score(path: Path) -> tuple[int, int, str]:
            name = path.name.lower()
            metal = 1 if "dummy_metal" in name else 0
            ye = 1 if "_ye" in name else 0
            return (-metal, ye, name)

        for path in sorted(candidates, key=score):
            yield path


def resolve_profile(
    explicit: str | Path | None = None,
    deck: str | Path | None = None,
    layer_map: str | Path | None = None,
) -> Discovery:
    """Find the profile for the current project without asking the user."""
    if explicit:
        return Discovery(load_profile(_absolute(explicit)), "profile")

    deck_value = deck or _env("DMYFILL_FILE")
    map_value = layer_map or _env("TECH_LAYER_MAP")
    resolved_map = _absolute(map_value) if map_value else None
    if deck_value:
        source = _rule_path(str(deck_value))
        profile = profile_from_deck(source, resolved_map)
        return Discovery(profile, "rule", source)

    for source in _pdk_deck_candidates():
        profile = profile_from_deck(source, resolved_map)
        return Discovery(profile, "pdk", source)

    raise DiscoveryError(
        "DMYFILL_FILE is not set and no PDK Calibre/dummy_util deck was found"
    )


def describe(discovery: Discovery) -> dict[str, Any]:
    """Return the stable fields used by the form and command line."""
    if discovery.rule is not None:
        rule = discovery.rule
        return {
            "profile": "",
            "rule": str(rule.path),
            "source": discovery.source,
            "deck": "",
            "deck_source": str(rule.path),
            "rule_source": str(rule.path),
            "fill_source": "rule",
            "fill_source_path": str(rule.path),
            "layer_map": str(rule.layer_map),
            "region_margin": 0.0,
            "note": discovery.note,
            "layers": list(rule.layer_names),
            "warnings": list(discovery.warnings),
        }
    profile = discovery.profile
    if profile is None:
        raise DiscoveryError("resolved fill source has neither deck nor rule")
    return {
        "profile": str(profile.path),
        "rule": str(discovery.deck_source) if discovery.deck_source else str(profile.deck),
        "source": discovery.source,
        "deck": str(profile.deck),
        "deck_source": str(discovery.deck_source) if discovery.deck_source else "",
        "fill_source": "deck",
        "fill_source_path": str(profile.deck),
        "layer_map": str(profile.layer_map) if profile.layer_map else "",
        "region_margin": profile.region_margin,
        "note": discovery.note,
        "layers": list(profile.layer_names),
        "warnings": list(discovery.warnings),
    }
