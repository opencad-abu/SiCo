"""Derived query rows reproduce verified results and never outlive changed bytes."""

from pathlib import Path

import pytest
from test_circuit_templates import legacy_rows, write_capture
from test_template_standard_migrate import AT

from cadai.template_build import make_template, publish
from cadai.template_capture import read_capture
from cadai.template_catalog import TemplateCatalog
from cadai.template_schema import TemplateError
from cadai.template_standard import derived, migrate
from cadai.template_standard.reader import query, snapshot


def staged_root(tmp_path):
    """One migrated package in a private standard root of its own."""
    capture_path = write_capture(tmp_path, legacy_rows())
    raw = capture_path.read_bytes()
    record = make_template(
        read_capture(capture_path), "digital", {"origin": "fixture", "version": "1"}
    )
    publish([record], tmp_path / "catalog.sqlite3")
    converted = migrate.convert(
        raw,
        record["template_ref"],
        category=record["category"],
        provenance=record["provenance"],
        at=AT,
    )
    root = tmp_path / "standard"
    installed = migrate.install(root, tmp_path / "evidence", converted)
    return root, converted, installed


def test_derived_rows_match_the_verified_query(tmp_path):
    root, converted, _ = staged_root(tmp_path)
    catalog = TemplateCatalog(root=root)
    ref = migrate.template_ref(converted)
    verified = query(catalog, dict(purpose="investigate", limit=100))
    rows, revision = derived.rows(catalog, rebuild=True)
    assert sorted(rows) == [ref]
    assert rows[ref]["capabilities"] == {
        name: check["result"]
        for name, check in converted["docs"]["qualification"]["checks"].items()
    }
    assert verified["total"] == 1
    assert verified["revision"] == revision
    assert (root / derived.FILE).exists()
    # Serving derived rows re-verifies every recorded digest.
    served, served_revision = derived.rows(catalog)
    assert served == rows and served_revision == revision
    assert query(catalog, dict(purpose="investigate", limit=100))["total"] == 1
    assert query(catalog, dict(purpose="reference", limit=100))["total"] == 0


def test_changed_bytes_invalidate_the_derived_rows(tmp_path):
    root, _, installed = staged_root(tmp_path)
    catalog = TemplateCatalog(root=root)
    derived.rows(catalog, rebuild=True)
    template = Path(installed) / "template.json"
    template.write_text(template.read_text() + "\n")
    assert derived.current(root) is None
    # A rebuilt cache must not hide a manifest conflict from the caller.
    with pytest.raises(TemplateError):
        snapshot(catalog)


@pytest.mark.parametrize('changed', ['index', 'package', 'content'])
def test_warm_derived_cache_rechecks_changes_without_a_time_window(tmp_path, changed):
    root, _, installed = staged_root(tmp_path)
    catalog = TemplateCatalog(root=root)
    derived.rows(catalog, rebuild=True)
    assert derived.current(root) is not None  # Warm any process-local cache.
    target = {'index': root / 'index.json', 'package': Path(installed) / 'package.json',
              'content': Path(installed) / 'template.json'}[changed]
    target.write_bytes(target.read_bytes() + b'\n')
    assert derived.current(root) is None
