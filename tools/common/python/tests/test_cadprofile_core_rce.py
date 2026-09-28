from cadprofile_profile_fixtures import *

def test_versioned_rce_rejects_legacy_enum_aliases(
    tmp_path: Path,
) -> None:
    source = _write(
        tmp_path / "rce.toml",
        _metadata("RCE") + '''[run]
root = "runs"
[input]
type = "OA"
[lvs]
tool = "Calibre"
[extract]
tool = "QRC"
corners = ["typ", "cmax"]
corner_temperatures = ["25", "125"]
top_cell_source = "schematic"
name_source = "layout"
output_type = "view"
[extract.view]
kind = "smart"
[runtime]
lvs_cpus = "1"
ext_cpus = "1"
''',
    )

    with pytest.raises(ProfileError, match="extract.top_cell_source must be one of"):
        load_profile(source, "RCE")


def test_versioned_rce_multiple_corner_scope_rejects_empty_selection(
    tmp_path: Path,
) -> None:
    source = _write(
        tmp_path / "rce.toml",
        _metadata("RCE")
        + '''[run]
root = "runs"
[input]
type = "OA"
[lvs]
tool = "Calibre"
[extract]
corner_scope = "Multiple Corners"
corners = []
corner_temperatures = []
[runtime]
lvs_cpus = "1"
ext_cpus = "1"
''',
    )

    with pytest.raises(ProfileError, match="at least one corner"):
        load_profile(source, "RCE")


def test_versioned_rce_single_corner_scope_rejects_multiple_selections(
    tmp_path: Path,
) -> None:
    source = _write(
        tmp_path / "rce.toml",
        _metadata("RCE")
        + '''[run]
root = "runs"
[input]
type = "OA"
[lvs]
tool = "Calibre"
[extract]
corner_scope = "Single Corner"
corner = "typ"
corners = ["typ", "cmax"]
corner_temperatures = ["25", "125"]
[runtime]
lvs_cpus = "1"
ext_cpus = "1"
''',
    )

    with pytest.raises(ProfileError, match="at most one corner"):
        load_profile(source, "RCE")


def test_versioned_rce_single_corner_scalar_and_array_must_match(
    tmp_path: Path,
) -> None:
    source = _write(
        tmp_path / "rce.toml",
        _metadata("RCE")
        + '''[run]
root = "runs"
[input]
type = "OA"
[lvs]
tool = "Calibre"
[extract]
corner_scope = "Single Corner"
corner = "typ"
corners = ["cmax"]
corner_temperatures = ["125"]
[runtime]
lvs_cpus = "1"
ext_cpus = "1"
''',
    )

    with pytest.raises(ProfileError, match="must match"):
        load_profile(source, "RCE")


def test_empty_optional_path_stays_empty(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "drc.toml",
        _drc_profile().replace(
            'runset_file = "${PDK_ROOT}/Calibre/DRC/rules.drc"',
            'runset_file = ""',
        ),
    )

    values = load_profile(source, "DRC").as_dict()

    assert values["drc.runset_file.raw"] == ""
    assert values["drc.runset_file.resolved"] == ""


def test_flow_defaults_do_not_leak_unrelated_sections(tmp_path: Path) -> None:
    values = load_profile(_write(tmp_path / "drc.toml", _drc_profile()), "DRC").as_dict()

    assert "lvs.hcell_enable" not in values
    assert "extract.start_rve" not in values
    assert "steps.abstract" not in values

    lef = _write(
        tmp_path / "lef-profile.toml",
        _metadata("LEF")
        + """[run]
root = "runs"
[input]
library = "demo"
cell = "INVX1"
[abstract]
[output]
geometry = true
technology = false
""",
    )
    lef_values = load_profile(lef, "LEF").as_dict()
    assert not any(path.startswith("batch.") for path in lef_values)


def test_profile_normalizes_integer_text_values(
    tmp_path: Path,
) -> None:
    source = _write(
        tmp_path / "normalized.toml",
        _drc_profile().replace('cpus = "4"', "cpus = 8"),
    )

    document = load_profile(source, "DRC")
    values = document.as_dict()
    published = render_profile_toml(document)

    assert values["runtime.cpus"] == "8"
    assert 'cpus = "8"' in published


def test_profile_rejects_legacy_gui_enum_spelling(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "legacy-enum.toml",
        _drc_profile().replace('type = "OA"', 'type = " oa "'),
    )

    with pytest.raises(ProfileError, match="Unsupported input.type"):
        load_profile(source, "DRC")


def test_profile_normalizes_whitespace_in_positive_integer_text(
    tmp_path: Path,
) -> None:
    source = _write(
        tmp_path / "parallel.toml",
        _drc_profile(
            "\n[batch]\n"
            'scope = "Multiple Cells"\n'
            'parallel_cells = " 03 "\n'
            'tasks = [["lib", "cell", "layout"]]\n'
        ),
    )

    document = load_profile(source, "DRC")

    assert document.as_dict()["batch.parallel_cells"] == "3"
    assert 'parallel_cells = "3"' in render_profile_toml(document)
