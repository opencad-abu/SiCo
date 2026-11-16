"""The builtin installer owns only the standard payload and never the catalogs."""

import importlib.util
import json
from pathlib import Path

from test_template_standard_derived import staged_root

from cadai.template_catalog import TemplateCatalog
from cadai.template_standard import derived
from cadai.template_standard.reader import query

REFERENCE = Path(__file__).resolve().parents[2] / "reference/circuit_templates"


def installer():
    spec = importlib.util.spec_from_file_location(
        "builtin_installer", REFERENCE / "install_standard.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def builtin_target(tmp_path):
    """A target with a legacy catalog and an unrelated file that must survive."""
    target = tmp_path / "builtin"
    (target / "catalogs").mkdir(parents=True)
    (target / "catalogs" / "legacy.sqlite3").write_bytes(b"legacy catalog")
    (target / "README.md").write_text("kept\n")
    return target


def test_install_and_remove_keep_foreign_files(tmp_path):
    module = installer()
    staging, converted, _ = staged_root(tmp_path)
    target = builtin_target(tmp_path)
    manifest = module.apply(staging, target, evidence="/private/evidence")
    assert manifest["templates"] == 1
    assert manifest["derived_rows"] == 1
    assert (target / "index.json").exists()
    assert (target / derived.FILE).exists()
    assert (target / "catalogs" / "legacy.sqlite3").read_bytes() == b"legacy catalog"
    assert (target / "README.md").read_text() == "kept\n"
    assert module.check(staging, target) == 0
    rows, _ = derived.rows(TemplateCatalog(root=target))
    assert list(rows) == [converted["tables"]["template"]["items"]["main"]["template_ref"]]
    # Standard queries answer from the installed payload, not from the catalogs.
    assert query(TemplateCatalog(root=target), dict(purpose="investigate", limit=10))[
        "total"
    ] == 1
    removed = module.remove(target)
    assert "index.json" in removed and derived.FILE in removed
    assert not (target / "index.json").exists()
    assert (target / "catalogs" / "legacy.sqlite3").exists()
    assert not (target / module.MANIFEST).exists()


def test_reinstall_removes_only_its_own_stale_entries(tmp_path):
    module = installer()
    staging, _, _ = staged_root(tmp_path)
    target = builtin_target(tmp_path)
    module.apply(staging, target)
    # An older installation owned this revision; the new one must drop it and
    # keep every foreign file.
    stale = target / ("f" * 64)
    stale.mkdir()
    (stale / "package.json").write_text(json.dumps({"format": "stale"}))
    (target / "foreign.txt").write_text("keep\n")
    value = json.loads((target / module.MANIFEST).read_text())
    value["payload"] = sorted(value["payload"] + [stale.name])
    (target / module.MANIFEST).write_text(json.dumps(value, indent=1, sort_keys=True))
    module.apply(staging, target)
    assert not stale.exists()
    assert (target / "foreign.txt").read_text() == "keep\n"
    value = json.loads((target / module.MANIFEST).read_text())
    assert "legacy.sqlite3" not in " ".join(value["payload"])
    assert "foreign.txt" not in value["payload"]
