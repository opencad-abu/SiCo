from cadview_catalog_fixtures import *

def test_catalog_category_and_combine_metadata_round_trip(tmp_path: Path) -> None:
    category = CatalogCategory("mos", ("nmos", "pmos"))
    library = CatalogLibrary(
        "PDK",
        tmp_path / "PDK",
        False,
        categories=(category,),
        combine_members=("smic28", "smic28hkmg"),
    )
    catalog = Catalog(
        tmp_path / "cds.lib",
        (library,),
        combine_groups=(CatalogCombineGroup("PDK", library.combine_members),),
    )

    decoded = Catalog.from_dict(catalog.to_dict())

    assert decoded == catalog
    assert decoded.library("PDK").categories[0].members == ("nmos", "pmos")

def test_explicit_empty_combine_groups_round_trip_without_promotion(
    tmp_path: Path,
) -> None:
    library = CatalogLibrary(
        "TOP",
        tmp_path / "TOP",
        False,
        combine_members=("member",),
    )
    catalog = Catalog(tmp_path / "cds.lib", (library,))

    assert Catalog.from_dict(catalog.to_dict()) == catalog

def test_catalog_model_is_deeply_immutable_and_json_round_trips(tmp_path: Path) -> None:
    cds, _ = _library_tree(tmp_path)
    view = CatalogView("schematic", tmp_path / "lib/cell/schematic")
    cell = CatalogCell("cell", tmp_path / "lib/cell", (view,))
    library = CatalogLibrary("lib", tmp_path / "lib", True, (cell,))
    catalog = Catalog(cds, (library,), authoritative=False)

    with pytest.raises(AttributeError):
        catalog.authoritative = True  # type: ignore[misc]
    with pytest.raises(AttributeError):
        catalog.libraries[0].cells += (cell,)  # type: ignore[misc]

    decoded = Catalog.from_dict(catalog.to_dict())
    assert decoded == catalog
    assert json.loads(json.dumps(catalog.to_dict()))["schema_version"] == 1
