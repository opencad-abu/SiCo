from cadprofile_profile_fixtures import *

@pytest.mark.parametrize(
    ("flow", "base", "unknown"),
    (
        ("DRC", _drc_profile(), "[drc]\nrunset_typo = \"bad\"\n"),
        (
            "LEF",
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
            "[abstract]\noption_typo = \"bad\"\n",
        ),
    ),
)
def test_versioned_profile_rejects_unknown_option_inside_allowed_section(
    tmp_path: Path, flow: str, base: str, unknown: str
) -> None:
    if flow == "DRC":
        text = base.replace("[drc]\n", unknown)
    else:
        text = base.replace("[abstract]\n", unknown)
    source = _write(tmp_path / f"{flow.lower()}-unknown.toml", text)

    with pytest.raises(ProfileError, match="Unknown profile option"):
        load_profile(source, flow)


def test_versioned_profile_rejects_retired_lsf_interactive_option(
    tmp_path: Path,
) -> None:
    source = _write(
        tmp_path / "drc-retired-lsf-option.toml",
        _drc_profile().replace(
            'server_name = "localhost"',
            'server_name = "localhost"\nlsf_interactive = true',
        ),
    )

    with pytest.raises(ProfileError, match="Unknown profile option"):
        load_profile(source, "DRC")


def test_execution_toml_is_not_accepted_as_profile(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "legacy-rce.toml",
        """[run]
run_dir = "runs/top"
[input]
type = "oa"
[lvs]
tool = "Calibre"
[extract]
corner = "typ"
xrc = { rule_file = "corner.rule" }
[runtime]
lvs_cpus = 2
ext_cpus = " 04 "
""",
    )

    with pytest.raises(ProfileError, match=r"section \[cad_config\] is required"):
        load_profile(source, "RCE")


def test_rejects_wrong_flow_future_version_unknown_sections_and_types(
    tmp_path: Path,
) -> None:
    wrong = _write(tmp_path / "wrong.toml", _drc_profile())
    with pytest.raises(ProfileError, match="not requested flow"):
        load_profile(wrong, "LVS")

    future = _write(tmp_path / "future.toml", _drc_profile().replace("version = 1", "version = 2"))
    with pytest.raises(ProfileError, match="schema version 2"):
        load_profile(future, "DRC")

    unknown = _write(tmp_path / "unknown.toml", _drc_profile("\n[secret]\nx = 1\n"))
    with pytest.raises(ProfileError, match="Unknown top-level"):
        load_profile(unknown, "DRC")

    bad_type = _write(
        tmp_path / "type.toml",
        _drc_profile().replace("rule_select_enable = true", 'rule_select_enable = "yes"'),
    )
    with pytest.raises(ProfileError, match="must be true or false"):
        load_profile(bad_type, "DRC")

    float_value = _write(
        tmp_path / "float.toml", _drc_profile().replace('cpus = "4"', "cpus = 4.0")
    )
    with pytest.raises(ProfileError, match="positive integer"):
        load_profile(float_value, "DRC")

    numeric_tool = _write(
        tmp_path / "numeric-tool.toml",
        _drc_profile().replace('tool = "Calibre"', "tool = 7"),
    )
    with pytest.raises(ProfileError, match="drc.tool must be a string"):
        load_profile(numeric_tool, "DRC")

    unknown_tool = _write(
        tmp_path / "unknown-tool.toml",
        _drc_profile().replace('tool = "Calibre"', 'tool = "bogus"'),
    )
    with pytest.raises(ProfileError, match="drc.tool must be one of"):
        load_profile(unknown_tool, "DRC")

    legacy_profile_key = _write(
        tmp_path / "legacy-profile-key.toml",
        _drc_profile().replace(_metadata("DRC"), ""),
    )
    with pytest.raises(ProfileError, match=r"section \[cad_config\] is required"):
        load_profile(legacy_profile_key, "DRC")
