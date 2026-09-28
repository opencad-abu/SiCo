from __future__ import annotations

from flow_profile_adapter_fixtures import *

def test_full_rce_reduction_profile_round_trips_through_shared_protocol(
    tmp_path: Path,
) -> None:
    selection = tmp_path / "reduce.sel"
    canonical = tmp_path / "canonical.dev"
    selection.write_text("--reduce_nets VDD\n", encoding="utf-8")
    canonical.write_text("rpoly\ncmim\n", encoding="utf-8")
    source = tmp_path / "rce-profile.toml"
    source.write_text(
        """[cad_config]
format = "sico-flow-profile"
version = 1
flow = "RCE"

[run]
root_type = "Customize Directory"
root = "/runs"
run_type = "Current Host"

[input]
type = "CCI"

[lvs]
tool = "Calibre"

[extract]
tool = "QRC"
tech_name = "typ"
tech_dir = "/pdk/qrc"
corner_scope = "Single Corner"
corner = "typ"
corners = ["typ"]
temperature = "25"
corner_temperatures = ["25"]
rc_type = "R+Cg+Cc"
top_cell_source = "Schematic"
name_source = "Schematic"
output_type = "sp"
start_rve = false

[reduction]
enabled = true
mode = "Selection File"
output_tag = "reduced_1"
control = "0.5"
delay_rel = "0.05"
delay_abs = "1e-12"
frequency = "20"
temperature = ""
ground = "VSS"
reduce_negative = true
selection_file = "reduce.sel"
canonical_device_file = "canonical.dev"

[runtime]
lvs_cpus = "1"
ext_cpus = "4"
""",
        encoding="utf-8",
    )

    document = load_profile(source, "RCE")
    data = document.as_dict()
    rendered = render_form_data(document)

    for path in RCE_FIELDS & {
        field for field in RCE_FIELDS if field.startswith("reduction.")
    }:
        assert path in data
    assert data["reduction.enabled"] is True
    assert data["reduction.reduce_negative"] is True
    assert data["reduction.selection_file"] == "reduce.sel"
    assert data["reduction.selection_file.resolved"] == str(selection)
    assert data["reduction.canonical_device_file.resolved"] == str(canonical)
    assert 'list("reduction.mode" "Selection File")' in rendered
    assert f'list("reduction.selection_file.resolved" "{selection}")' in rendered


@pytest.mark.parametrize(
    ("old", "new", "message"),
    (
        ('mode = "Selection File"', 'mode = "Unsupported"', "reduction.mode"),
        ('output_tag = "reduced_1"', 'output_tag = "bad-tag"', "output_tag"),
        ('control = "0.5"', 'control = "1.5"', "reduction.control"),
        ('frequency = "20"', 'frequency = "0"', "reduction.frequency"),
        ('temperature = ""', 'temperature = "25"', "reduction.temperature"),
    ),
)
def test_rce_reduction_profile_rejects_invalid_settings(
    tmp_path: Path, old: str, new: str, message: str
) -> None:
    selection = tmp_path / "reduce.sel"
    canonical = tmp_path / "canonical.dev"
    selection.write_text("--reduce_nets VDD\n", encoding="utf-8")
    canonical.write_text("rpoly\n", encoding="utf-8")
    source = tmp_path / "rce-profile.toml"
    source.write_text(
        """[cad_config]
format = "sico-flow-profile"
version = 1
flow = "RCE"

[run]
root_type = "Customize Directory"
root = "/runs"

[input]
type = "CCI"

[lvs]
tool = "Calibre"

[extract]
tool = "QRC"
corner = "typ"
output_type = "sp"

[reduction]
enabled = true
mode = "Selection File"
output_tag = "reduced_1"
control = "0.5"
delay_rel = "0.05"
delay_abs = "1e-12"
frequency = "20"
temperature = ""
reduce_negative = false
selection_file = "reduce.sel"
canonical_device_file = "canonical.dev"

[runtime]
lvs_cpus = "1"
ext_cpus = "1"
""".replace(old, new),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        load_profile(source, "RCE")


@pytest.mark.parametrize(
    ("output_type", "tool", "temperature", "message"),
    (
        ("smartview", "QRC", "25", None),
        ("extview", "QRC", "", None),
        ("extview", "QRC", "25", "reduction.temperature"),
        ("smartview", "StarRC", "", "Enabled reduction requires"),
        ("extview", "StarRC", "", "Enabled reduction requires"),
    ),
)
def test_rce_reduction_profile_honors_legacy_qrc_view_aliases(
    tmp_path: Path,
    output_type: str,
    tool: str,
    temperature: str,
    message: str | None,
) -> None:
    source = tmp_path / f"{output_type}-{tool}.toml"
    source.write_text(
        f"""[cad_config]
format = "sico-flow-profile"
version = 1
flow = "RCE"

[run]
root_type = "Customize Directory"
root = "/runs"

[input]
type = "CCI"

[lvs]
tool = "Calibre"

[extract]
tool = "{tool}"
corner = "typ"
output_type = "{output_type}"

[reduction]
enabled = true
temperature = "{temperature}"

[runtime]
lvs_cpus = "1"
ext_cpus = "1"
""",
        encoding="utf-8",
    )

    if message is None:
        assert load_profile(source, "RCE").as_dict()["reduction.enabled"] is True
    else:
        with pytest.raises(ValueError, match=message):
            load_profile(source, "RCE")


def test_rce_reduction_profile_rejects_multiple_corner_scope(tmp_path: Path) -> None:
    source = tmp_path / "rce-multi-corner-reduction.toml"
    source.write_text(
        """[cad_config]
format = "sico-flow-profile"
version = 1
flow = "RCE"

[run]
root_type = "Customize Directory"
root = "/runs"

[input]
type = "CCI"

[lvs]
tool = "Calibre"

[extract]
tool = "QRC"
corner_scope = "Multiple Corners"
corner = "Cmin"
corners = ["Cmin", "Cmax"]
corner_temperatures = ["25", "125"]
output_type = "dspf"

[reduction]
enabled = true

[runtime]
lvs_cpus = "1"
ext_cpus = "1"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="disabled for Multiple Corners"):
        load_profile(source, "RCE")


def test_full_lvs_profile_round_trips_through_shared_protocol(tmp_path: Path) -> None:
    source = tmp_path / "lvs-profile.toml"
    source.write_text(
        """[cad_config]
format = "sico-flow-profile"
version = 1
flow = "LVS"

[run]
root_type = "Customize Directory"
root = "/runs"
run_type = "LSF Farm"
queue_name = "normal"
server_name = "host1"

[input]
type = "OA"
cdl_include_enable = true

[input.schematic]
lib = "work"
cell = "top"
view = "schematic"
cdl_header_file = "/pdk/header.cdl"

[input.layout]
lib = "work"
cell = "top"
view = "layout"

[input.cdl]
file = "/data/top.cdl"
cell = "top"

[input.gds]
file = "/data/top.gds"
cell = "TOP"

[lvs]
tool = "Calibre"
runset_name = "default"
runset_file = "/pdk/lvs.rule"
run_mode = "Hier"
hcell_enable = true
hcell_file = "/pdk/hcell"
ignore_error = false
case_sensitive = true
virtual_connect_enable = false
virtual_connect_name_enable = true
virtual_connect_names = "?"
custom_svrf_enable = true
custom_svrf_command = "LVS FILTER UNUSED OPTION"

[runtime]
lvs_cpus = "4"

[batch]
scope = "Multiple Cells"
parallel_cells = "2"
tasks = [["work", "a", "schematic", "work", "a", "layout"]]
""",
        encoding="utf-8",
    )

    document = load_profile(source, "LVS")
    data = document.as_dict()
    rendered = render_form_data(document)

    assert set(LVS_FIELDS) <= set(data)
    assert data["batch.tasks"] == (
        ("work", "a", "schematic", "work", "a", "layout"),
    )
    assert data["lvs.svdb_query"] == ()
    assert 'list("lvs.svdb_query" list())' in rendered
    assert 'list("lvs.virtual_connect_enable" nil)' in rendered
    assert 'list("input.cdl_include_enable" t)' in rendered
    assert (
        'list("batch.tasks" list(list("work" "a" "schematic" '
        '"work" "a" "layout")))'
    ) in rendered
