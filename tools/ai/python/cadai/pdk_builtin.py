"""Portable, tool-owned metadata for the two standard Cadence device libraries."""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import gzip
import json
from pathlib import Path

from . import pdk_enrich
from .pdk_normalize import detail, digest
from .pdk_schema import MAX_CACHE_BYTES, SECTIONS, PdkUnavailable

LIBRARIES = frozenset({"analogLib", "basic"})
FORMAT = "cad.pdk.builtin.v1"
ROOT = Path(__file__).resolve().parents[2] / "share" / "pdk-data"
LIBRARY_PATH = "@library@"
RAW_FIELDS = frozenset({"identity", "cdf", "database", "project_models"})


def relocate(value, library_path):
    """Expand only the recorded library placeholder, including nested raw metadata."""
    if isinstance(value, str):
        return value.replace(LIBRARY_PATH, library_path)
    if isinstance(value, list):
        return [relocate(item, library_path) for item in value]
    if isinstance(value, dict):
        return {key: relocate(item, library_path) for key, item in value.items()}
    return value


@lru_cache(maxsize=2)
def _read(path, stamp):
    try:
        with gzip.open(path, "rb") as stream:
            raw = stream.read(MAX_CACHE_BYTES + 1)
        if len(raw) > MAX_CACHE_BYTES:
            raise ValueError("builtin data exceeds its size limit")
        value = json.loads(raw)
        payload = value["payload"]
        library = payload["library"]
        if (value["format"] != FORMAT or library not in LIBRARIES
                or Path(path).name != library + ".json.gz" or payload["view"] != "symbol"
                or value["digest"] != digest(payload)):
            raise ValueError("builtin identity/digest mismatch")
        from .pdk_data import checked_directory
        checked_directory(payload["directory"], library, "symbol")
        targets = {row["identity"]["target"]["cell"]: row["identity"]["target"]
                   for row in payload["directory"]["items"]}
        if not targets or set(targets) != set(payload["devices"]):
            raise ValueError("builtin device coverage mismatch")
        for cell, device in payload["devices"].items():
            if set(device) != RAW_FIELDS or device["identity"]["target"] != targets[cell]:
                raise ValueError("invalid builtin device")
        return value
    except (OSError, EOFError, KeyError, TypeError, ValueError) as exc:
        raise PdkUnavailable("builtin_pdk_unavailable", "Invalid built-in PDK data: " + str(path)) from exc


class BuiltinPdk:
    def __init__(self):
        self.catalogs = {}

    def bundle(self, library):
        path = ROOT / (library + ".json.gz")
        try:
            stat = path.stat()
            stamp = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            return _read(str(path), stamp)
        except OSError as exc:
            raise PdkUnavailable("builtin_pdk_unavailable", "Built-in PDK data is missing: " + str(path)) from exc

    @staticmethod
    def _device(bundle, ctx, target):
        lib = next(row for row in ctx["libraries"] if row["name"] == target["library"])
        payload = bundle["payload"]
        raw = relocate(payload["devices"][target["cell"]], lib["resolved_path"])
        value = detail(raw, ctx)
        # Timestamps describe the bundled observation, not a live device capture.
        value["captured_at"] = payload["captured_at"]
        categories = pdk_enrich.device_categories(payload["categories"], target["cell"])
        model = next((p["default"]["value"] for p in value["parameters"]["items"]
                      if p["name"] == "model"), None)
        resolution = {"status": "decks_unavailable", "queried": model}
        return pdk_enrich.enrich(value, resolution, categories, {"available": False})

    def discover(self, ctx, view):
        from .pdk_data import PdkData, device_fingerprint
        found = {}
        if view != "symbol":
            return found
        for lib in ctx["libraries"]:
            library = lib["name"]
            if (library not in LIBRARIES or lib.get("status") != "complete"
                    or not lib.get("resolved_path")):
                continue
            bundle = self.bundle(library)
            key = (library, digest(lib), bundle["digest"])
            if key not in self.catalogs:
                directory = relocate(bundle["payload"]["directory"], lib["resolved_path"])
                devices = {}
                for record in directory["items"]:
                    target = record["identity"]["target"]
                    value = self._device(bundle, ctx, target)
                    devices[target["cell"]] = {
                        "digest": device_fingerprint(value), "revision": value["revision"],
                        "incomplete_sections": [part for part in SECTIONS if value[part]["status"] != "complete"],
                        "categories": value["categories"]["all"], "enriched": True,
                        "tiers": {tier: sorted(p["name"] for p in value["parameters"]["items"]
                                               if p.get("tier") == tier)
                                  for tier in ("interface", "derived", "auxiliary")},
                        "ranges": [], "readiness": value["readiness"]["overall"],
                        "model": value["model_resolution"].get("queried"),
                        "model_status": "decks_unavailable", "model_decks": 0, "limits": False,
                    }
                payload = {"library": library, "view": view, "directory": directory,
                           "devices": devices, "excluded": {}, "coverage": PdkData.coverage(devices),
                           "captured_at": bundle["payload"]["captured_at"]}
                if len(self.catalogs) >= 8:
                    self.catalogs.clear()
                self.catalogs[key] = {"source": "builtin", "complete": True,
                                      "path": str(ROOT / (library + ".json.gz")),
                                      "schema": FORMAT, "digest": digest(payload), "payload": payload}
            found[library] = self.catalogs[key]
        return found

    def device(self, ctx, target, expected_digest):
        from .pdk_data import device_fingerprint
        try:
            value = self._device(self.bundle(target["library"]), ctx, target)
        except (KeyError, StopIteration) as exc:
            raise PdkUnavailable("builtin_pdk_unavailable", "Built-in device is unavailable") from exc
        if device_fingerprint(value) != expected_digest:
            raise PdkUnavailable("snapshot_changed", "Built-in PDK data changed; start a new search")
        return deepcopy(value)
