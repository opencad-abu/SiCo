"""Default edits affect generated tools without taking ownership of GUI choices."""

from pathlib import Path
from types import MappingProxyType

import pytest

from caddefaults import Defaults
from caddefaults.source import Source
from rcepy.gen_qrc import generate_qrc
from rcepy.gen_query import generate_query
from rcepy.generators import generate_all
from rcepy.gen_starrc import generate_starrc
from rcepy.qrc_defaults import QrcDefaults, qrc_launch_arguments
from rcepy.starrc_defaults import MIGRATED_FIELDS
from qrc_netlist_customize_fixtures import _config as qrc_config
from starrc_netlist_customize_fixtures import _config as star_config


def bundle(name, text):
    return Defaults(MappingProxyType({name: Source(name, Path("/defaults") / name, text.encode())}))


def test_quantus_default_is_used_only_for_active_extraction(tmp_path):
    cfg = qrc_config(tmp_path).replace("selection", value={
        "net_enable": True, "net_type": "Exclude Nets", "nets": "VDD",
    })
    text = generate_qrc(cfg, cfg.context(), defaults=bundle(
        "quantus.options", "[extract]\n-extract_via_cap false\n"
    )).read_text()
    commands = [line for line in text.splitlines() if line.startswith("extract ")]
    assert len(commands) == 2
    assert "-extract_via_cap false" in commands[0]
    assert "-type \"none\"" in commands[1] and "-extract_via_cap" not in commands[1]
    assert "-extract_gate_diffusion_fringing_cap" not in text
    assert "-net_property_value" not in text


def test_quantus_mode_default_and_duplicate_detection():
    defaults = QrcDefaults(bundle("quantus.options", '[output_db]\n-add_explicit_vias true\n[output_db.file]\n-sub_node_char "#"\n'))
    assert defaults.options("output_db", "file")[-1] == '-sub_node_char "#"'
    assert len(defaults.options("output_db", "smart_view")) == 1
    bad = QrcDefaults(bundle("quantus.options", '[output_db]\n-add_explicit_vias true\n[output_db.file]\n-add_explicit_vias false\n'))
    with pytest.raises(ValueError, match="repeat"):
        bad.options("output_db", "file")


@pytest.mark.parametrize("text", [
    "[extract]\n-type r_only", "[input_db]\n-directory_name /other",
    "[output_db.file]\n-disable_instances true", "[unknown]\n-x 1",
    "[extract]\n-extract_via_cap true\n[extract]\n-extract_via_cap false",
    '[extract]\n-extract_via_cap true; source bad',
    '[extract]\n-extract_via_cap true -type none',
    '[extraction_setup]\n-parasitic_blocking_device_cells_type white',
    '[extraction_setup.block_cells]\n-parasitic_blocking_device_cells_file other',
    '[process_technology]\n-technology_name OTHER',
    '[output_db.smart_view]\n-hierarchy_delimiter .',
    '[input_db]\n-net_property_value 5 6',
    '[input_db]\n-instance_property_value text',
    '[process_technology.multi_corner]\n-technology_name "two names"',
])
def test_quantus_rejects_conflicts_and_invalid_fragments(text):
    with pytest.raises(ValueError):
        QrcDefaults(bundle("quantus.options", text))


def test_starrc_edit_and_gui_geometry_requirements(tmp_path):
    cfg = star_config(tmp_path, netlist={"parasitic_coordinates": True})
    defaults = bundle("starrc.options", "[extraction]\nMODE: 500\nREDUCTION: YES\n")
    text = generate_starrc(cfg, cfg.context(), defaults=defaults).read_text()
    assert "MODE: 500" in text
    assert text.count("REDUCTION: NO") == 2  # REDUCTION and POWER_REDUCTION
    assert "REDUCTION: YES" not in text
    assert "DENSITY_BASED_THICKNESS" not in text
    assert "NETLIST_FORMAT: SPF" in text


@pytest.mark.parametrize("field", sorted(MIGRATED_FIELDS))
def test_starrc_old_behavior_fields_have_actionable_migration_error(tmp_path, field):
    cfg = star_config(tmp_path).replace("extract", "starrc", field, value="YES")
    with pytest.raises(ValueError, match=r"moved to .*starrc.options"):
        generate_starrc(cfg, cfg.context(), defaults=bundle("starrc.options", ""))


def test_starrc_rejects_gui_override_and_preserves_pdk_checks(tmp_path):
    cfg = star_config(tmp_path, netlist={"parasitic_coordinates": True})
    with pytest.raises(ValueError, match="owned"):
        generate_starrc(cfg, cfg.context(), defaults=bundle(
            "starrc.options", "[extraction]\nNETLIST_FORMAT: SPEF\n"
        ))
    (tmp_path / "tech/RCmax/common.opt").write_text("REDUCTION: YES\n")
    with pytest.raises(ValueError, match="common.opt"):
        generate_starrc(cfg, cfg.context(), defaults=bundle("starrc.options", ""))


def test_quantus_license_wait_is_argv_not_shell():
    assert qrc_launch_arguments(bundle("quantus.launch.args", "-lic_queue\n45\n")) == ["-lic_queue", "45"]
    assert qrc_launch_arguments(bundle("quantus.launch.args", "")) == []
    with pytest.raises(ValueError):
        qrc_launch_arguments(bundle("quantus.launch.args", "-cmd\nother.ccl"))


@pytest.mark.parametrize("kind", ["white", "gray"])
@pytest.mark.parametrize("blocked", [True, False])
def test_quantus_blocking_type_comes_from_conditional_defaults(tmp_path, kind, blocked):
    cfg = qrc_config(tmp_path).replace("selection", value={
        "cell_enable": blocked, "cell_type": "Block Cells", "cells": "macro",
    })
    text = generate_qrc(cfg, cfg.context(), defaults=bundle(
        "quantus.options",
        f"[extraction_setup.block_cells]\n-parasitic_blocking_device_cells_type {kind}\n",
    )).read_text()
    assert (f"-parasitic_blocking_device_cells_type {kind}" in text) == blocked
    assert ("-parasitic_blocking_device_cells_file" in text) == blocked
    assert text.count("extraction_setup ") == int(blocked)


def test_quantus_empty_defaults_do_not_restore_fixed_options(tmp_path):
    cfg = qrc_config(tmp_path).replace("selection", value={
        "cell_enable": True, "cell_type": "Block Cells", "cells": "macro",
    })
    defaults = bundle("quantus.options", "")
    text = generate_qrc(cfg, cfg.context(), defaults=defaults).read_text()
    query = generate_query(cfg, cfg.context(), defaults=defaults).read_text()
    for option in (
        "parasitic_blocking_device_cells_type", "net_property_value",
        "instance_property_value", "device_property_value",
        "hierarchy_delimiter", "temporary_directory_name",
    ):
        assert "-" + option not in text
    assert "gds netprop number" not in query
    assert "gds placeprop number" not in query
    assert "gds devprop number" not in query
    assert "-parasitic_blocking_device_cells_file" in text
    assert text.count("-net_name_space") == 1  # GUI output namespace only.


def test_quantus_edited_input_properties_are_shared_with_calibre_query(tmp_path):
    cfg = qrc_config(tmp_path)
    defaults = bundle("quantus.options", """[input_db]
-net_property_value 15
-instance_property_value 16
-device_property_value 17
-hierarchy_delimiter "."
[extraction_setup]
-net_name_space "LAYOUT"
[output_setup]
-temporary_directory_name "siteScratch"
""")
    text = generate_qrc(cfg, cfg.context(), defaults=defaults).read_text()
    query = generate_query(cfg, cfg.context(), defaults=defaults).read_text()
    for option, command, value in (
        ("net_property_value", "netprop", 15),
        ("instance_property_value", "placeprop", 16),
        ("device_property_value", "devprop", 17),
    ):
        assert f"-{option} {value}" in text
        assert f"gds {command} number {value}" in query
    assert '-hierarchy_delimiter "."' in text
    assert '-net_name_space "LAYOUT"' in text
    assert '-temporary_directory_name "siteScratch"' in text


@pytest.mark.parametrize("custom", [True, False])
def test_quantus_gui_delimiter_replaces_file_default_once(tmp_path, custom):
    cfg = qrc_config(tmp_path, netlist={
        "hierarchy_delimiter_enable": custom, "hierarchy_delimiter": "|",
    })
    text = generate_qrc(cfg, cfg.context(), defaults=bundle(
        "quantus.options", '[output_db.file]\n-hierarchy_delimiter "."\n'
    )).read_text()
    expected = "|" if custom else "."
    assert f'-hierarchy_delimiter "{expected}"' in text
    assert text.count("-hierarchy_delimiter") == 1


def test_quantus_multi_corner_name_is_shared_with_technology_definition(tmp_path):
    cfg = qrc_config(tmp_path).replace("extract", "corners", value=["RCmax", "RCmin"])
    corner = tmp_path / "tech/RCmin"
    corner.mkdir()
    (corner / "qrcTechFile").write_text("tech\n")
    defaults = bundle("quantus.options", "[process_technology.multi_corner]\n-technology_name SITE_MPC\n")
    text = generate_qrc(cfg, cfg.context(), defaults=defaults).read_text()
    assert text.count("-technology_name SITE_MPC") == 1
    assert "DEFINE SITE_MPC " in (cfg.context().log_dir / "qrc_mpc_techlib.defs").read_text()
    single = cfg.replace("extract", "corners", value=["RCmax"])
    assert "-technology_name" not in generate_qrc(single, single.context(), defaults=defaults).read_text()
    with pytest.raises(ValueError, match="requires one -technology_name"):
        generate_qrc(cfg, cfg.context(), defaults=bundle("quantus.options", ""))


def test_quantus_query_and_extract_share_one_snapshot_per_request(tmp_path, monkeypatch):
    cfg = qrc_config(tmp_path).replace("input", value={
        "type": "SVDB", "svdb": {"dir": str(tmp_path / "svdb"), "cell": "top"},
    })
    original_load = Defaults.load
    reads = []

    def load(cls, names):
        reads.append(tuple(names))
        sources = dict(original_load(names).sources)
        old = sources["quantus.options"]
        data = old.data.replace(b"-net_property_value 5", f"-net_property_value {len(reads) + 10}".encode())
        sources[old.name] = Source(old.name, old.path, data)
        return Defaults(MappingProxyType(sources))

    monkeypatch.setattr(Defaults, "load", classmethod(load))
    for expected in (11, 12):
        outputs = generate_all(cfg, cfg.context())
        assert f"gds netprop number {expected}" in outputs["query"].read_text()
        assert f"-net_property_value {expected}" in outputs["extract"].read_text()
    assert len(reads) == 2
