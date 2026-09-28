from cadprofile_profile_fixtures import *

@pytest.mark.parametrize(
    ("flow", "relative"),
    (
        ("RCE", "rce/python/examples/and2x1h7.toml"),
        ("LEF", "lef/python/examples/ic618_rak_invx1.toml"),
    ),
)
def test_execution_toml_examples_are_rejected_as_profiles(
    flow: str, relative: str
) -> None:
    with pytest.raises(ProfileError, match=r"section \[cad_config\] is required"):
        load_profile(CAD_ROOT / relative, flow)


def test_unversioned_lef_execution_toml_is_rejected(
    tmp_path: Path,
) -> None:
    source = _write(
        tmp_path / "lef.toml",
        """[run]
run_dir = "runs/demo"
cds_lib = "cds.lib"

[input]
library = "demo"
cell = "INVX1"

[abstract]

[output]
lef_file = "INVX1.lef"
geometry = true
technology = false
""",
    )

    with pytest.raises(ProfileError, match=r"section \[cad_config\] is required"):
        load_profile(source, "LEF")


def test_versioned_lef_output_path_is_relative_to_profile(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "lef-profile.toml",
        _metadata("LEF")
        + """[run]
root = "runs"
run_type = "Current Host"

[input]
library = "demo"
cell = "INVX1"

[abstract]

[output]
lef_file = "INVX1.lef"
geometry = true
technology = false
""",
    )

    values = load_profile(source, "LEF").as_dict()

    assert values["output.lef_file.resolved"] == str(tmp_path / "INVX1.lef")


def test_versioned_lef_rejects_derived_run_directory(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "lef-profile.toml",
        _metadata("LEF")
        + """[run]
root = "runs"
run_dir = "runs/demo"
run_type = "Current Host"

[input]
library = "demo"
cell = "INVX1"

[abstract]

[output]
lef_file = "INVX1.lef"
geometry = true
technology = false
""",
    )

    with pytest.raises(ProfileError, match="Unknown profile option.*run.run_dir"):
        load_profile(source, "LEF")


def test_drc_and_lvs_execution_toml_are_rejected_as_profiles(tmp_path: Path) -> None:
    base = (
        "[run]\n"
        'run_dir = "missing/run"\n'
        'run_type = "Current Host"\n\n'
        "[input]\n"
        'type = "OA"\n\n'
        "[input.layout]\n"
        'lib = "lib"\ncell = "top"\nview = "layout"\n\n'
    )
    drc = base + (
        "[drc]\n"
        'run_mode = "Hier"\n'
        "rule_select_enable = false\n"
        "rule_select_groups = []\n"
        "rule_select_checks = []\n\n"
        "[runtime]\ncpus = \"1\"\n"
    )
    lvs = base + (
        "[input.schematic]\nlib = \"lib\"\ncell = \"top\"\nview = \"schematic\"\n\n"
        "[lvs]\nrun_mode = \"Hier\"\nhcell_enable = false\n\n"
        "[runtime]\nlvs_cpus = \"1\"\n"
    )

    for flow, text in (("DRC", drc), ("LVS", lvs)):
        with pytest.raises(ProfileError, match=r"section \[cad_config\] is required"):
            load_profile(_write(tmp_path / f"{flow.lower()}.toml", text), flow)
