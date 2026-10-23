"""Portable, tool-owned standard data for the two standard Cadence device libraries.

The bundled data at ``tools/ai/share/pdk-data`` is a standard root
(``index.json`` + one published package per library).  The legacy compressed
capture format is retired: the catalogs below are projected from the standard
documents so that selection and search keep serving the two base libraries
without a live device scan, and ``install`` copies the published baseline into
a workspace when the generation flow is asked to prepare one of them.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from .pdk_normalize import digest
from .pdk_schema import PdkUnavailable

LIBRARIES = frozenset({"analogLib", "basic"})
ROOT = Path(__file__).resolve().parents[2] / "share" / "pdk-data"
INDEX = "index.json"
SCHEMA = "sico.pdk.package"


def _missing(detail):
    return PdkUnavailable("builtin_pdk_unavailable", "Invalid built-in PDK data: " + detail)


def _absent(path):
    return PdkUnavailable("builtin_pdk_unavailable", "Built-in PDK data is missing: " + path)


class BuiltinPdk:
    def __init__(self):
        self._index = None

    def index(self):
        """The bundled standard packages, keyed by library, cached by file stamp."""
        path = ROOT / INDEX
        try:
            stat = path.stat()
            stamp = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            raise _absent(str(path)) from None
        if self._index is None or self._index[0] != stamp:
            try:
                value = json.loads(path.read_text())
                packages = value["packages"]
                if value.get("format") != "sico.pdk.index" or not isinstance(packages, dict):
                    raise ValueError("index format")
                entries = {}
                for library, row in packages.items():
                    if library not in LIBRARIES:
                        continue
                    target = ROOT / row["path"]
                    if not (target / "package.json").is_file():
                        raise ValueError("package missing: " + str(target))
                    entries[library] = {"path": row["path"],
                                        "roots": dict(row.get("roots") or {})}
                if not entries:
                    raise ValueError("no standard libraries")
            except (OSError, KeyError, TypeError, ValueError) as exc:
                raise _missing(str(path)) from exc
            self._index = (stamp, entries)
        return deepcopy(self._index[1])

    def package(self, library):
        from .pdk_standard.package import Package

        entry = self.index().get(library)
        if entry is None:
            raise _missing("no bundled package for " + str(library))
        return Package(ROOT / entry["path"])

    def symbol(self, library, cell):
        """The published symbol facts (bbox/anchors) of one bundled device."""
        package = self.package(library)
        row = package.document("device.json")["items"].get(cell) or {}
        if not row.get("dir") or not package.available(row["dir"] + "/symbol.json"):
            raise _missing("device not published: " + str(cell))
        return package.document(row["dir"] + "/symbol.json")

    def install(self, root, library, roots):
        """Install the bundled published baseline into a workspace generation root."""
        from .pdk_standard.package import install as package_install

        return package_install(root, library, ROOT / self.index()[library]["path"], roots)

    def rebind(self, root, library, roots):
        """Re-point the installed baseline at the live site path; same content only."""
        from .pdk_standard.package import rebind as package_rebind

        package = self.package(library)
        return package_rebind(root, library, package.manifest_digest, roots)

    def catalog(self, library):
        """The legacy catalog entry for one library, projected from the standard package."""
        from .pdk_standard.readiness import issues as device_issues

        package = self.package(library)
        devices = package.document("device.json")["items"]
        grouped = {}
        for issue in device_issues(package):
            grouped.setdefault(issue["device"], set()).add(issue["file"])
        directory, rows = [], {}
        for cell, row in sorted(devices.items()):
            name = row["dir"]
            cdf = package.document(name + "/cdf.json")
            names = sorted(cdf["parameters"])
            directory.append({"identity": {"target": {"library": library, "cell": cell,
                                                      "view": "symbol"}},
                              "cdf_summary": {"status": "complete", "presence": cdf["presence"],
                                              "names": names}})
            allowed = any(row["use"][purpose] == "allow"
                          for purpose in ("circuit", "testbench"))
            sections = sorted({part.split("/")[-1].split(".")[0]
                               for part in grouped.get(cell, set())}) if allowed else ["usage"]
            model = (cdf["parameters"].get("model") or {}).get("default")
            rows[cell] = {
                "digest": digest([library, cell, names, row["categories"], sections]),
                "revision": digest([library, cell, names, row["categories"], sections]),
                "incomplete_sections": sections,
                "categories": list(row["categories"]),
                "enriched": True,
                "tiers": {"interface": sorted(cdf.get("interface_parameters") or []),
                          "derived": [], "auxiliary": []},
                "ranges": [],
                "readiness": ("incomplete" if sections else "ready") if allowed else "unpublished",
                "model": model if isinstance(model, str) else None,
                "model_status": self._model_status(package, row),
                "model_decks": 0, "limits": False,
            }
        from .pdk_data import PdkData

        payload = {"library": library, "view": "symbol",
                   "directory": {"status": "complete", "truncated": False, "issues": [],
                                 "items": directory},
                   "devices": rows, "excluded": {},
                   "coverage": PdkData.coverage(rows)}
        return {"source": "builtin", "complete": True,
                "path": str(ROOT / self.index()[library]["path"]),
                "schema": SCHEMA, "digest": digest(payload), "payload": payload}

    @staticmethod
    def _model_status(package, row):
        name = row["dir"] + "/simulation.json"
        if not package.available(name):
            return "missing"
        interfaces = package.document(name)["interfaces"]
        if not interfaces:
            return "none"
        return ("builtin" if all(i["model_source"] == "builtin" for i in interfaces.values())
                else "external")

    def discover(self, ctx, view):
        found = {}
        if view != "symbol":
            return found
        for lib in ctx["libraries"]:
            library = lib["name"]
            if (library not in LIBRARIES or lib.get("status") != "complete"
                    or not lib.get("resolved_path")):
                continue
            found[library] = self.catalog(library)
        return found
