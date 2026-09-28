"""Opt-in imports verify registered aliases retain exact object identity."""

import importlib
import json
import os
from pathlib import Path

import pytest

from utility.a_philosophy_compat import load_compatibility

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = load_compatibility(ROOT)
PARTS = MANIFEST["includes"]
pytestmark = pytest.mark.skipif(
    os.environ.get("CAD_RUN_COMPAT_SOURCE_IMPORTS") != "1",
    reason="explicit source import test needs product dependencies including Qt",
)


def module_name(path):
    for base in MANIFEST["search_paths"]:
        try:
            relative = Path(path).relative_to(base)
        except ValueError:
            continue
        return ".".join(relative.with_suffix("").parts).removesuffix(".__init__")
    raise ValueError(path)


@pytest.mark.parametrize("manifest", PARTS, ids=lambda value: Path(value).stem)
def test_registered_exports_refer_to_the_exact_owner_objects(manifest, monkeypatch):
    for path in MANIFEST["search_paths"]:
        monkeypatch.syspath_prepend(str(ROOT / path))
    for entry in json.loads((ROOT / manifest).read_text())["compatibility"]:
        if "exports" not in entry:
            continue
        source = importlib.import_module(module_name(entry["source"]))
        owner = importlib.import_module(module_name(entry["replacement"]))
        for name, target in entry["exports"].items():
            assert getattr(source, name) is getattr(owner, target), (entry["legacy"], name)
