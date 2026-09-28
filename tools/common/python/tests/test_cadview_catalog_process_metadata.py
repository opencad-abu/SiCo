from cadview_catalog_fixtures import *

def test_dbaccess_catalog_decodes_authoritative_json_and_command(tmp_path: Path) -> None:
    cds, _ = _library_tree(tmp_path)
    executable = _write_provider(tmp_path / "dbAccess")
    script = tmp_path / "catalog.il"
    script.write_text("printf(\\\"fixture\\\")\\n", encoding="utf-8")

    catalog = dbaccess_catalog(cds, executable=str(executable), script=script)

    assert catalog.authoritative is True
    assert catalog.provider == "dbAccess"
    assert [library.name for library in catalog.libraries] == ["alib", "zlib"]
    assert catalog.library("zlib").cells[0].views[0].name == "layout"

def test_dbaccess_catalog_fills_missing_combine_metadata_from_cdslib(
    tmp_path: Path,
) -> None:
    for name in ("member", "TOP"):
        (tmp_path / name).mkdir()
    cds = tmp_path / "cds.lib"
    cds.write_text(
        "DEFINE member ./member\n"
        "DEFINE TOP ./TOP\n"
        "ASSIGN TOP COMBINE member\n",
        encoding="utf-8",
    )
    payload = {
        "schema_version": 1,
        "authoritative": True,
        "libraries": [
            {
                "name": name,
                "path": str(tmp_path / name),
                "writable": False,
                "cells": [],
            }
            for name in ("member", "TOP")
        ],
    }
    executable = _write_json_provider(tmp_path / "dbAccess", payload)
    script = tmp_path / "catalog.il"
    script.write_text("fixture", encoding="utf-8")

    catalog = dbaccess_catalog(cds, executable=str(executable), script=script)

    assert catalog.combine_groups == (
        CatalogCombineGroup("TOP", ("member",)),
    )
    assert catalog.library("TOP").combine_members == ("member",)

def test_dbaccess_catalog_merges_category_branches_missing_from_ddcat(
    tmp_path: Path,
) -> None:
    library_path = tmp_path / "PDK"
    for name in ("nmos", "pmos"):
        (library_path / name / "schematic").mkdir(parents=True)
    (library_path / "PDK.TopCat").write_text(
        'PDK/devices.Cat type="category"\n', encoding="utf-8"
    )
    (library_path / "devices.Cat").write_text(
        'PDK/nmos type="cell"\nPDK/sub.Cat type="category"\n',
        encoding="utf-8",
    )
    (library_path / "sub.Cat").write_text(
        'PDK/pmos type="cell"\n', encoding="utf-8"
    )
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE PDK ./PDK\n", encoding="utf-8")
    payload = {
        "schema_version": 1,
        "authoritative": True,
        "libraries": [
            {
                "name": "PDK",
                "path": str(library_path),
                "writable": False,
                "cells": [
                    {"name": name, "views": ["schematic"]}
                    for name in ("nmos", "pmos")
                ],
                # Simulate a partial ddCat traversal: the root succeeded but
                # opening its child failed, so the child is absent from JSON.
                "categories": [
                    {"name": "devices", "members": ["nmos"], "children": []}
                ],
            }
        ],
    }
    executable = _write_json_provider(tmp_path / "dbAccess", payload)
    script = tmp_path / "catalog.il"
    script.write_text("fixture", encoding="utf-8")

    catalog = dbaccess_catalog(cds, executable=str(executable), script=script)

    assert catalog.library("PDK").categories == (
        CatalogCategory(
            "devices",
            ("nmos",),
            (CatalogCategory("sub", ("pmos",)),),
        ),
    )
