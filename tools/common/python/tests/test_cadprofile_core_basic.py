from cadprofile_profile_fixtures import *

def test_drc_profile_normalizes_to_stable_flat_entries(tmp_path: Path) -> None:
    source = _write(tmp_path / "drc.toml", _drc_profile())

    document = load_profile(source, "drc")

    assert document.flow == "DRC"
    assert document.version == 1
    assert document.as_dict()["profile.path"] == str(source.resolve())
    assert document.as_dict()["drc.rule_select_groups"] == ("METAL",)
    assert document.as_dict()["run.root"] == "${DRC_DB_DIR}"
    assert "run.lsf_interactive" not in document.as_dict()
    assert document.as_dict()["drc.runset_file.raw"].startswith("${PDK_ROOT}")
    assert document.as_dict()["drc.runset_file.resolved"] == ""
    assert tuple(path for path, _ in document.entries) == tuple(
        sorted(path for path, _ in document.entries)
    )


def test_skill_output_is_inert_and_escapes_strings(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "drc.toml",
        _drc_profile().replace(
            'custom_svrf_command = ""',
            'custom_svrf_command = "\\\")) system(\\\"touch /tmp/pwned\\\") ; \\\\"',
        ),
    )

    data = render_form_data(load_profile(source, "DRC"))

    assert data.startswith('SICO_profileSetLoadData("DRC" 1 list(\n')
    assert data.endswith("\n))\n")
    assert 'list("drc.rule_select_enable" t)' in data
    assert 'list("drc.rule_select_checks" list("M1.W.1"))' in data
    assert '\\")) system(\\\"touch /tmp/pwned\\\") ; \\\\' in data
    assert '\nCAD_profileSetLoadData' not in data[1:]


def test_form_data_is_private_exclusive_and_atomic(tmp_path: Path) -> None:
    document = load_profile(_write(tmp_path / "drc.toml", _drc_profile()), "DRC")
    output = tmp_path / "form.il"

    assert write_form_data(document, output) == output
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".form.il.*.tmp"))

    with pytest.raises(FileExistsError):
        write_form_data(document, output)
    assert output.read_text(encoding="utf-8") == render_form_data(document)


def test_publish_normalizes_metadata_and_preserves_multiline_strings(
    tmp_path: Path,
) -> None:
    source = _write(
        tmp_path / "source.toml",
        _drc_profile().replace(
            'custom_svrf_command = ""',
            'custom_svrf_command = "DRC SELECT CHECK M1\\nDRC MAXIMUM RESULTS ALL"',
        ),
    )
    document = load_profile(source, "DRC")
    target = tmp_path / "published.toml"

    publish_profile(document, target)
    published = target.read_text(encoding="utf-8")

    assert published.startswith(
        '[cad_config]\nformat = "sico-flow-profile"\nversion = 1\nflow = "DRC"\n'
    )
    assert 'custom_svrf_command = "DRC SELECT CHECK M1\\nDRC MAXIMUM' in published
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert load_profile(target, "DRC").as_dict()["drc.custom_svrf_command"] == (
        "DRC SELECT CHECK M1\nDRC MAXIMUM RESULTS ALL"
    )

    target.write_text("old\n", encoding="utf-8")
    publish_profile(document, target)
    assert target.read_text(encoding="utf-8") == render_profile_toml(document)
    assert not list(tmp_path.glob(".published.toml.*.tmp"))


def test_publish_source_validates_and_normalizes_before_replacing_target(
    tmp_path: Path,
) -> None:
    source = _write(tmp_path / "source.toml", _drc_profile())
    target = _write(tmp_path / "published.toml", "old\n")

    publish_profile_source(source, target, flow="DRC")

    assert target.read_text(encoding="utf-8") == render_profile_toml(
        load_profile(source, "DRC")
    )

    invalid = _write(
        tmp_path / "invalid.toml",
        _drc_profile().replace("rule_select_enable = true", 'rule_select_enable = "yes"'),
    )
    published = target.read_text(encoding="utf-8")
    with pytest.raises(ProfileError, match="must be true or false"):
        publish_profile_source(invalid, target, flow="DRC")
    assert target.read_text(encoding="utf-8") == published


def test_publish_replace_failure_preserves_target_and_removes_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = load_profile(_write(tmp_path / "source.toml", _drc_profile()), "DRC")
    target = _write(tmp_path / "published.toml", "old\n")

    def reject_replace(source: object, destination: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("cadgui.transfer.os.replace", reject_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        publish_profile(document, target)

    assert target.read_text(encoding="utf-8") == "old\n"
    assert not list(tmp_path.glob(".published.toml.*.tmp"))


def test_relative_paths_resolve_beside_profile_but_name_lists_stay_inline(
    tmp_path: Path,
) -> None:
    source = _write(
        tmp_path / "rce.toml",
        _metadata("RCE")
        + """[run]
root = "runs"
run_type = "Current Host"
[input]
type = "OA"
[lvs]
tool = "Calibre"
[extract]
tech_dir = "tech"
corners = ["cworst"]
corner_temperatures = ["125"]
[runtime]
lvs_cpus = "1"
ext_cpus = "1"
[selection]
nets = "VDD VSS"
cells = "FILL* TAP*"
""",
    )

    document = load_profile(source, "RCE")
    values = document.as_dict()
    assert values["run.root.raw"] == "runs"
    assert values["run.root.resolved"] == str(tmp_path / "runs")
    assert values["extract.tech_dir.resolved"] == str(tmp_path / "tech")
    assert "selection.nets.resolved" not in values
    assert values["selection.nets"] == "VDD VSS"


def test_rce_selection_file_resolves_only_when_profile_relative_file_exists(
    tmp_path: Path,
) -> None:
    selection_file = _write(tmp_path / "nets.list", "VDD\n")
    source = _write(
        tmp_path / "rce.toml",
        _metadata("RCE")
        + f'''[run]
root = "runs"
[input]
type = "OA"
[lvs]
tool = "Calibre"
[extract]
corners = ["typ"]
corner_temperatures = ["25"]
[runtime]
lvs_cpus = "1"
ext_cpus = "1"
[selection]
nets = "{selection_file.name}"
cells = "FILL* TAP*"
''',
    )

    values = load_profile(source, "RCE").as_dict()

    assert values["selection.nets.raw"] == "nets.list"
    assert values["selection.nets.resolved"] == str(selection_file.resolve())
    assert "selection.cells.resolved" not in values
