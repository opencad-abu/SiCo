from cadprofile_profile_fixtures import *

@pytest.mark.parametrize(
    ("flow", "source_text", "message"),
    (
        (
            "RCE",
            _metadata("RCE")
            + """[run]
root = "runs"
[input]
type = "OA"
[lvs]
tool = "Calibre"
[extract]
tool = "PVS"
[runtime]
lvs_cpus = "1"
ext_cpus = "1"
""",
            "extract.tool must be one of",
        ),
        (
            "LEF",
            _metadata("LEF")
            + """[run]
root = "runs"
[input]
library = "demo"
cell = "INVX1"
[abstract]
bin = "Macro"
[output]
geometry = true
technology = false
""",
            "abstract.bin must be one of",
        ),
    ),
)
def test_profile_rejects_values_unavailable_in_gui_choices(
    tmp_path: Path, flow: str, source_text: str, message: str
) -> None:
    source = _write(tmp_path / f"{flow.lower()}-choice.toml", source_text)

    with pytest.raises(ProfileError, match=message):
        load_profile(source, flow)


@pytest.mark.parametrize(
    "path",
    (
        "input.cdl_include_enable",
        "lvs.virtual_connect_enable",
        "lvs.virtual_connect_name_enable",
        "filter.cap_percentage_enable",
        "filter.cap_value_enable",
        "filter.res_value_enable",
    ),
)
def test_rce_profile_rejects_non_boolean_enable_flags(
    tmp_path: Path, path: str
) -> None:
    section, key = path.split(".", 1)
    source = _write(
        tmp_path / f"{section}-{key}.toml",
        _metadata("RCE")
        + f"""[run]
root = "runs"
run_type = "Current Host"
[input]
type = "OA"
{f'{key} = "yes"' if section == 'input' else ''}
[lvs]
run_mode = "Hier"
{f'{key} = "yes"' if section == 'lvs' else ''}
[extract]
corners = ["typ"]
corner_temperatures = ["25"]
[runtime]
lvs_cpus = "1"
ext_cpus = "1"
[filter]
{f'{key} = "yes"' if section == 'filter' else ''}
""",
    )

    with pytest.raises(ProfileError, match=rf"{re.escape(path)} must be true or false"):
        load_profile(source, "RCE")


def test_rce_profile_uses_scalar_corner_when_corner_list_is_absent_or_empty(
    tmp_path: Path,
) -> None:
    base = _metadata("RCE") + """[run]
root = "runs"
run_type = "Current Host"
[input]
type = "OA"
[lvs]
tool = "Calibre"
[extract]
corner = "typ"
temperature = "25"
[runtime]
lvs_cpus = "1"
ext_cpus = "1"
"""
    absent = load_profile(_write(tmp_path / "absent.toml", base), "RCE").as_dict()
    empty = load_profile(
        _write(
            tmp_path / "empty.toml",
            base.replace(
                'corner = "typ"', 'corner = "typ"\ncorners = []\ncorner_temperatures = []'
            ),
        ),
        "RCE",
    ).as_dict()

    assert absent["extract.corners"] == ()
    assert empty["extract.corners"] == ()
    assert empty["extract.corner_temperatures"] == ()


@pytest.mark.parametrize("corners", ("", "corners = []\ncorner_temperatures = []\n"))
def test_rce_profile_rejects_absent_corner_selection(
    tmp_path: Path, corners: str
) -> None:
    source = _write(
        tmp_path / f"rce-no-corner-{bool(corners)}.toml",
        _metadata("RCE")
        + f'''[run]
root = "runs"
[input]
type = "OA"
[lvs]
tool = "Calibre"
[extract]
{corners}[runtime]
lvs_cpus = "1"
ext_cpus = "1"
''',
    )

    with pytest.raises(ProfileError, match="must contain a valid corner"):
        load_profile(source, "RCE")


def test_rejects_invalid_batch_rows_without_touching_paths(tmp_path: Path) -> None:
    valid = _drc_profile(
        "\n[batch]\n"
        'scope = "Multiple Cells"\n'
        'parallel_cells = "2"\n'
        'tasks = [["lib", "cell", "layout"]]\n'
    )
    document = load_profile(_write(tmp_path / "valid.toml", valid), "DRC")
    assert document.as_dict()["batch.tasks"] == (("lib", "cell", "layout"),)

    invalid = valid.replace(
        'tasks = [["lib", "cell", "layout"]]', 'tasks = [["lib", "cell"]]'
    )
    with pytest.raises(ProfileError, match="exactly 3 strings"):
        load_profile(_write(tmp_path / "invalid.toml", invalid), "DRC")
