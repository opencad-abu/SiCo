from cadview_catalog_fixtures import *

def test_filesystem_catalog_reads_library_manager_category_files(tmp_path: Path) -> None:
    library_path = tmp_path / "PDK"
    library_path.mkdir()
    for cell in ("nmos", "pmos", "other"):
        (library_path / cell / "schematic").mkdir(parents=True)
    (library_path / "PDK.TopCat").write_text(
        'TDMCHECKPOINT="1.0"\n'
        'PDK/mos.Cat type="category"\n'
        'PDK/other.Cat type="category"\n',
        encoding="utf-8",
    )
    (library_path / "mos.Cat").write_text(
        'TDMCHECKPOINT="1.0"\n'
        'PDK/nmos type="cell"\n'
        'PDK/pmos type="cell"\n'
        'PDK/missing type="cell"\n',
        encoding="utf-8",
    )
    (library_path / "other.Cat").write_text(
        'TDMCHECKPOINT="1.0"\nPDK/other type="cell"\n',
        encoding="utf-8",
    )
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE PDK ./PDK\n", encoding="utf-8")

    catalog = filesystem_catalog(cds)
    categories = catalog.library("PDK").categories

    assert [(item.name, item.members) for item in categories] == [
        ("mos", ("nmos", "pmos")),
        ("other", ("other",)),
    ]

def test_filesystem_catalog_category_files_reject_wrong_library_entries(
    tmp_path: Path,
) -> None:
    library_path = tmp_path / "PDK"
    (library_path / "nmos" / "schematic").mkdir(parents=True)
    (library_path / "pmos" / "schematic").mkdir(parents=True)
    (library_path / "PDK.TopCat").write_text(
        'TDMCHECKPOINT="1.0"\n'
        'PDK/devices.Cat type="category"\n'
        'OTHER/shadow.Cat type="category"\n',
        encoding="utf-8",
    )
    (library_path / "devices.Cat").write_text(
        'TDMCHECKPOINT="1.0"\n'
        'PDK/nmos type="cell"\n'
        'OTHER/pmos type="cell"\n'
        'PDK/sub.Cat type="category"\n'
        'OTHER/wrongSub.Cat type="category"\n',
        encoding="utf-8",
    )
    (library_path / "sub.Cat").write_text(
        'TDMCHECKPOINT="1.0"\nPDK/pmos type="cell"\n',
        encoding="utf-8",
    )
    # Same-named local files prove that the rejected OTHER-qualified records
    # cannot accidentally escape qualification by being reduced to basename.
    (library_path / "shadow.Cat").write_text(
        'TDMCHECKPOINT="1.0"\nPDK/pmos type="cell"\n',
        encoding="utf-8",
    )
    (library_path / "wrongSub.Cat").write_text(
        'TDMCHECKPOINT="1.0"\nPDK/nmos type="cell"\n',
        encoding="utf-8",
    )
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE PDK ./PDK\n", encoding="utf-8")

    catalog = filesystem_catalog(cds)
    categories = catalog.library("PDK").categories

    assert categories == (
        CatalogCategory(
            "devices",
            ("nmos",),
            (CatalogCategory("sub", ("pmos",)),),
        ),
    )

def test_filesystem_catalog_category_cycles_are_bounded_and_deduplicated(
    tmp_path: Path,
) -> None:
    library_path = tmp_path / "PDK"
    (library_path / "nmos" / "schematic").mkdir(parents=True)
    (library_path / "pmos" / "schematic").mkdir(parents=True)
    (library_path / "PDK.TopCat").write_text(
        'PDK/A.Cat type="category"\n'
        'PDK/A.Cat type="category"\n',
        encoding="utf-8",
    )
    (library_path / "A.Cat").write_text(
        'PDK/nmos type="cell"\n'
        'PDK/A.Cat type="category"\n'
        'PDK/B.Cat type="category"\n'
        'PDK/B.Cat type="category"\n',
        encoding="utf-8",
    )
    (library_path / "B.Cat").write_text(
        'PDK/pmos type="cell"\nPDK/A.Cat type="category"\n',
        encoding="utf-8",
    )
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE PDK ./PDK\n", encoding="utf-8")

    catalog = filesystem_catalog(cds)

    assert catalog.library("PDK").categories == (
        CatalogCategory(
            "A",
            ("nmos",),
            (CatalogCategory("B", ("pmos",)),),
        ),
    )

def test_filesystem_catalog_ignores_category_symlinks_outside_library(
    tmp_path: Path,
) -> None:
    library_path = tmp_path / "PDK"
    (library_path / "nmos" / "schematic").mkdir(parents=True)
    outside = tmp_path / "outside.Cat"
    outside.write_text('PDK/nmos type="cell"\n', encoding="utf-8")
    (library_path / "PDK.TopCat").write_text(
        'PDK/escaped.Cat type="category"\n'
        'PDK/loop.Cat type="category"\n',
        encoding="utf-8",
    )
    (library_path / "escaped.Cat").symlink_to(outside)
    (library_path / "loop.Cat").symlink_to("loop.Cat")
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE PDK ./PDK\n", encoding="utf-8")

    catalog = filesystem_catalog(cds)

    assert catalog.library("PDK").categories == ()
