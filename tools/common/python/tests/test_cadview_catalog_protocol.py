from cadview_catalog_fixtures import *

def test_bundled_dbaccess_protocol_uses_portable_dd_apis_and_json_escape() -> None:
    worker = Path(__file__).resolve().parents[2] / "skill/SICO_catalogWorker.il"
    scripts = (
        (
            worker.read_text(),
            "cadviewCatalogWriteCategory",
            "catalogPort category memberName firstChild",
        ),
    )
    for script, category_writer, recursive_arguments in scripts:
        assert "ddGetObjReadPath(lib)" in script
        assert "ddGetObjWritePath(lib)" in script
        assert "ddGetObjChildren(lib)" in script
        assert "ddGetObjChildren(cellId)" in script
        assert "ddGetObj(libName cellName)" not in script
        assert "ddGetObjType(cellId)" in script
        assert "ddGetObjType(viewId)" in script
        assert "lib~>readPath" not in script
        assert "lib~>writePath" not in script
        assert "lib~>cells" not in script
        assert "~>views" not in script
        assert "charToInt(getchar(text index))" in script
        assert "code<32" in script
        assert f"procedure({category_writer}" in script
        assert 'memberType=sprintf(nil "%s" cadr(member))' in script
        assert 'when(equal(memberType "cell")' in script
        assert 'when(equal(memberType "category")' in script
        assert '\\"children\\":[' in script
        assert recursive_arguments in script
        assert "isCallable('ddCatOpen)" in script
        assert "errset(apply('ddCatOpen" in script
        assert "isCallable('ddCatGetCatMembers)" in script
        assert "errset(apply('ddCatGetCatMembers" in script
        assert "isCallable('ddCatClose)" in script
        assert "errset(apply('ddCatClose list(category)) nil)" in script
        assert "activeCategories" in script
        assert "member(category activeCategories)" in script
        assert "length(activeCategories)>=64" in script
        assert "cons(category activeCategories)" in script
        # ddCatGetCatMembers returns (name type) pairs.  Serializing car(member)
        # without checking the type would put subcategory names in the cell
        # member array and lose the hierarchy.
        assert "cadviewCatalogJsonString(car(member))" not in script
        assert script.count("(") == script.count(")")

def test_bundled_dbaccess_protocol_smoke_against_real_catalog() -> None:
    """Exercise the generated SKILL in a detached real dbAccess process."""

    dbaccess = shutil.which(os.environ.get("CADVIEW_DBACCESS", "dbAccess"))
    cds_library_file = os.environ.get("CADVIEW_PROBE_CDS_LIB")
    if dbaccess is None or not cds_library_file:
        pytest.skip("dbAccess and CADVIEW_PROBE_CDS_LIB are required")

    catalog = dbaccess_catalog(cds_library_file, executable=dbaccess, timeout=60)

    assert catalog.authoritative is True
    assert catalog.provider == "dbAccess"
    assert catalog.libraries
    assert any(library.cells for library in catalog.libraries)
    assert any(
        cell.views
        for library in catalog.libraries
        for cell in library.cells
    )
