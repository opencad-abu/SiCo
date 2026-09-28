from cadview_catalog_fixtures import *

def test_filesystem_catalog_resolves_and_stably_sorts(tmp_path: Path) -> None:
    cds, work = _library_tree(tmp_path)

    catalog = filesystem_catalog(cds)

    assert catalog.authoritative is False
    assert catalog.provider == "filesystem"
    assert [library.name for library in catalog.libraries] == ["source", "work"]
    library = catalog.library("work")
    assert library is not None
    assert library.path == work.resolve()
    assert [cell.name for cell in library.cells] == ["acell", "zcell"]
    assert [view.name for view in library.cells[1].views] == ["layout", "schematic"]
    assert library.write_path == work.resolve()

def test_read_library_definitions_returns_immutable_domain_result(
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

    result = read_library_definitions(cds)

    assert isinstance(result, LibraryDefinitions)
    assert result.libraries["member"] == (tmp_path / "member").resolve()
    assert result.combine_groups["TOP"] == ("member",)
    with pytest.raises(TypeError):
        result.libraries["new"] = tmp_path  # type: ignore[index]
    with pytest.raises(TypeError):
        result.combine_groups["TOP"] = ()  # type: ignore[index]

def test_catalog_uses_public_cdslib_operation() -> None:
    source = Path(__file__).resolve().parents[1] / "cadview" / "catalog.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    private_imports = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "cdslib"
        and node.level == 1
        for alias in node.names
        if alias.name.startswith("_")
    ]
    assert private_imports == []

def test_filesystem_catalog_preserves_combined_library_hierarchy(tmp_path: Path) -> None:
    root = tmp_path
    for name in ("analogLib", "avTech", "basic", "cdsDefTechLib", "CADENCE"):
        (root / name).mkdir()
    cds = root / "cds.lib"
    cds.write_text(
        "DEFINE analogLib ./analogLib\n"
        "DEFINE avTech ./avTech\n"
        "DEFINE basic ./basic\n"
        "DEFINE cdsDefTechLib ./cdsDefTechLib\n"
        "DEFINE CADENCE ./CADENCE\n"
        "ASSIGN CADENCE COMBINE analogLib avTech basic cdsDefTechLib\n",
        encoding="utf-8",
    )

    catalog = filesystem_catalog(cds)

    group = catalog.combine_groups
    assert group == (
        CatalogCombineGroup(
            "CADENCE", ("analogLib", "avTech", "basic", "cdsDefTechLib")
        ),
    )
    combined = catalog.library("CADENCE")
    assert combined is not None
    assert combined.combine_members == group[0].members
    # The physical library names remain in the catalog and can still be
    # selected for operations that require a concrete OA destination.
    assert [item.name for item in catalog.libraries] == [
        "CADENCE",
        "analogLib",
        "avTech",
        "basic",
        "cdsDefTechLib",
    ]

def test_filesystem_catalog_reads_aassign_from_nested_include(tmp_path: Path) -> None:
    for name in ("lib1", "lib2", "TOP"):
        (tmp_path / name).mkdir()
    included = tmp_path / "included.cds.lib"
    included.write_text(
        "DEFINE lib1 ./lib1\n"
        "DEFINE lib2 ./lib2\n"
        "DEFINE TOP ./TOP\n"
        "AASSIGN TOP COMBINE lib1 lib2\n",
        encoding="utf-8",
    )
    cds = tmp_path / "cds.lib"
    cds.write_text("INCLUDE ./included.cds.lib\n", encoding="utf-8")

    catalog = filesystem_catalog(cds)

    assert catalog.combine_groups == (
        CatalogCombineGroup("TOP", ("lib1", "lib2")),
    )
    assert catalog.library("TOP").combine_members == ("lib1", "lib2")

def test_filesystem_catalog_unassign_removes_inherited_combine(tmp_path: Path) -> None:
    for name in ("lib1", "lib2", "TOP"):
        (tmp_path / name).mkdir()
    included = tmp_path / "included.cds.lib"
    included.write_text(
        "DEFINE lib1 ./lib1\n"
        "DEFINE lib2 ./lib2\n"
        "DEFINE TOP ./TOP\n"
        "ASSIGN TOP COMBINE lib1 lib2\n",
        encoding="utf-8",
    )
    cds = tmp_path / "cds.lib"
    cds.write_text(
        "INCLUDE ./included.cds.lib\nUNASSIGN TOP COMBINE\n",
        encoding="utf-8",
    )

    catalog = filesystem_catalog(cds)

    assert catalog.combine_groups == ()
    assert catalog.library("TOP").combine_members == ()

def test_filesystem_catalog_combine_keywords_are_case_insensitive(
    tmp_path: Path,
) -> None:
    for name in ("lib1", "TOP"):
        (tmp_path / name).mkdir()
    included = tmp_path / "included.cds.lib"
    included.write_text("uNaSsIgN TOP cOmBiNe\n", encoding="utf-8")
    cds = tmp_path / "cds.lib"
    cds.write_text(
        "dEfInE lib1 ./lib1\n"
        "dEfInE TOP ./TOP\n"
        "aSsIgN TOP cOmBiNe lib1\n"
        "iNcLuDe ./included.cds.lib\n",
        encoding="utf-8",
    )

    catalog = filesystem_catalog(cds)

    assert catalog.combine_groups == ()
    assert catalog.library("TOP").combine_members == ()

def test_filesystem_catalog_requires_combined_root_to_be_defined_first(
    tmp_path: Path,
) -> None:
    for name in ("member", "TOP"):
        (tmp_path / name).mkdir()
    cds = tmp_path / "cds.lib"
    cds.write_text(
        "DEFINE member ./member\n"
        "ASSIGN TOP COMBINE member\n"
        "DEFINE TOP ./TOP\n",
        encoding="utf-8",
    )

    catalog = filesystem_catalog(cds)

    assert catalog.combine_groups == ()
    assert catalog.library("TOP").combine_members == ()
