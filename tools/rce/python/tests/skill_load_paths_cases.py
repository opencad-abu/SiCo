import re

from skill_load_fixtures import (
    CAD_ROOT,
    _procedure_body,
)
from skill_test_support import read_skill_source


def test_gui_writers_normalize_run_dir_before_writing_toml() -> None:
    for relative, function in (
        ("rce/skill++/RCECB.ils", "rcePrintReserved"),
        ("lvs/skill++/LVSCB.ils", "lvsWriteToml"),
        ("lvs/skill++/STAGECB.ils", "lvsStageWriteToml"),
        ("drc/skill++/DRCCB.ils", "drcWriteToml"),
    ):
        text = read_skill_source(CAD_ROOT / relative)
        body = _procedure_body(text, function)
        normalize = body.index("runDir=simplifyFilename(runDir t)")
        if function == "rcePrintReserved":
            write = body.index("rceWriteRunToml(outFile form runDir cdslib)")
            assert 'SICO_tomlWriteString(outFile "run_dir" runDir)' in _procedure_body(text, "rceWriteRunToml")
        else:
            write = body.index('SICO_tomlWriteString(outFile "run_dir" runDir)')
        assert normalize < write
        if function == "rcePrintReserved":
            output = body.index("outPath=if(nativeView then")
            assert "RCE_tomlDefaultOutput(runDir topCell" in body[output:]
            assert normalize < output


def test_gui_writers_make_external_toml_paths_absolute() -> None:
    revision = "20260812.toml.absolute.paths.v1"
    writers = (
        (
            "drc/skill++/DRCCB.ils",
            "drcWriteToml",
            "drcAbsolutePathRevision",
            {
                "cds_lib": "cdslib",
                "layer_map": "layerMap",
                "runset_file": "runsetFile",
            },
        ),
        (
            "lvs/skill++/LVSCB.ils",
            "lvsWriteToml",
            "lvsAbsolutePathRevision",
            {
                "cds_lib": "cdslib",
                "layer_map": "layerMap",
                "runset_file": "runsetFile",
                "hcell_file": "hcellFile",
                "cdl_header_file": "cdlIncludeFile",
            },
        ),
        (
            "lvs/skill++/STAGECB.ils",
            "lvsStageWriteToml",
            "lvsStageAbsolutePathRevision",
            {
                "cds_lib": "cdslib",
                "layer_map": "layerMap",
                "cdl_header_file": "cdlIncludeFile",
            },
        ),
        (
            "rce/skill++/RCECB.ils",
            "rcePrintReserved",
            "rceAbsolutePathRevision",
            {
                "cds_lib": "cdslib",
                "layer_map": "layerMap",
                "runset_file": "runsetFile",
                "hcell_file": "hcellFile",
                "cdl_header_file": "cdlIncludeFile",
                "tech_dir": "techDir",
                "nets": "netsFile",
                "cells": "cellsFile",
                "pin_order_file": "pinControlFile",
            },
        ),
    )

    for relative, function, revision_function, path_fields in writers:
        text = read_skill_source(CAD_ROOT / relative)
        body = _procedure_body(text, function)
        assert f"procedure({revision_function}()" in text
        assert f'"{revision}"' in _procedure_body(text, revision_function)
        for key, variable in path_fields.items():
            write = f'SICO_tomlWriteString(outFile "{key}" {variable})'
            if function == "rcePrintReserved":
                helper = {
                    "cds_lib": "rceWriteRunToml", "layer_map": "rceWriteLayoutToml",
                    "runset_file": "rceWriteLvsToml", "hcell_file": "rceWriteLvsToml",
                    "cdl_header_file": "rceWriteSchematicToml", "tech_dir": "rceWriteExtractToml",
                    "nets": "rceWriteSelectionToml", "cells": "rceWriteSelectionToml",
                    "pin_order_file": "rceWriteNetlistToml",
                }[key]
                assert write in _procedure_body(text, helper)
                assert body.index(helper + "(") > body.index("SICO_absolutePath(")
                call = re.search(rf"{helper}\(([^)]+)\)", body).group(1)
                assert variable in call.split()
            else:
                assert write in body
                assert body.index(write) > body.index("SICO_absolutePath(")

    for entry_path, revision_function in (
        ("drc/skill++/DRC.ils", "drcAbsolutePathRevision"),
        ("lvs/skill++/LVS.ils", "lvsAbsolutePathRevision"),
        ("lvs/skill++/LVS.ils", "lvsStageAbsolutePathRevision"),
        ("rce/skill++/RCE.ils", "rceAbsolutePathRevision"),
    ):
        entry = (CAD_ROOT / entry_path).read_text(encoding="utf-8")
        assert f"isCallable('{revision_function})" in entry
        assert f'{revision_function}()=="{revision}"' in entry


def test_file_callbacks_and_gui_external_paths_use_shared_helpers() -> None:
    helper = read_skill_source(CAD_ROOT / "common/skill/SICO_toml.il")
    assert "procedure(SICO_fileStem(path)" in helper
    assert "name=car(last(parseString(value \"/\")))" in helper
    assert "procedure(SICO_absolutePath(path)" in helper
    assert "simplifyFilename(value t)" in helper

    for relative, prefix, writer in (
        ("rce/skill++/RCECB.ils", "rce", "rcePrintReserved"),
        ("lvs/skill++/LVSCB.ils", "lvs", "lvsWriteToml"),
    ):
        text = read_skill_source(CAD_ROOT / relative)
        assert "form~>cdlCell~>value=SICO_fileStem(form~>cdlFile~>value)" in text
        assert "form~>gdsCell~>value=SICO_fileStem(form~>gdsFile~>value)" in text
        assert "parseString (last (parseString" not in text

        body = _procedure_body(text, writer)
        cdl_normalize = "cdl=SICO_absolutePath(cdl)"
        gds_normalize = "gds=SICO_absolutePath(gds)"
        assert body.count(cdl_normalize) == 2
        assert body.count(gds_normalize) == 2
        if writer == "rcePrintReserved":
            assert body.index(cdl_normalize) < body.index("rceWriteCdlToml(outFile form cdl cdlcell)")
            assert body.index(gds_normalize) < body.index("rceWriteGdsToml(outFile gds gdscell)")
            assert 'SICO_tomlWriteString(outFile "file" cdl)' in _procedure_body(text, "rceWriteCdlToml")
            assert 'SICO_tomlWriteString(outFile "file" gds)' in _procedure_body(text, "rceWriteGdsToml")
        else:
            assert body.index(cdl_normalize) < body.index('SICO_tomlWriteString(outFile "file" cdl)')
            assert body.index(gds_normalize) < body.index('SICO_tomlWriteString(outFile "file" gds)')

        entry = read_skill_source(CAD_ROOT / f"{prefix}/skill++/{prefix.upper()}.ils")
        assert "isCallable('SICO_absolutePath)" in entry
        assert "isCallable('SICO_fileStem)" in entry


def test_standalone_gds_and_cdl_use_independent_project_roots() -> None:
    config = read_skill_source(CAD_ROOT / "lvs/skill++/STAGECFG.ils")
    callback = read_skill_source(CAD_ROOT / "lvs/skill++/STAGECB.ils")
    runner = (CAD_ROOT / "lvs/python/lvspy/single_stage.py").read_text(
        encoding="utf-8"
    )

    assert '"GDS_DB_DIR"' in config
    assert '"CDL_DB_DIR"' in config
    assert '"RCE_RUN_ROOT"' not in config
    assert '"LVS_DB_DIR"' not in config
    assert '"RCE_DB_DIR"' not in config
    assert 'lvsStageOutDirCB(hiGetCurrentForm() \\"gds\\")' in config
    assert 'lvsStageOutDirCB(hiGetCurrentForm() \\"cdl\\")' in config
    assert 'procedure(lvsStageOutputName' in callback
    assert '" && output=" SICO_shellQuote(outputName)' in callback
    assert "lvsStageBackupRunData(runPathPrefix stage outputName)" in callback

    assert "produced_file: Path" in runner
    assert "output_file: Path" in runner
    assert "self.ctx.run_dir / f\"{self.ctx.layout_cell}.gds\"" in runner
    assert "self.ctx.run_dir / f\"{self.ctx.source_cell}.cdl\"" in runner
    assert "publish_stage_output(" in runner
    assert 'self.ctx.log_dir / "stream_gds.toml"' in runner
    assert 'self.ctx.log_dir / "export_cdl.toml"' in runner


def test_standalone_stage_options_are_wired_to_both_forms_and_toml() -> None:
    gui = read_skill_source(CAD_ROOT / "lvs/skill++/STAGEGUI.ils")
    callback = read_skill_source(CAD_ROOT / "lvs/skill++/STAGECB.ils")

    assert "defclass(STAGEOPTIONSGUI ()" in gui
    assert gui.count("HARDWARE STAGEOPTIONSGUI)") == 2
    assert '?buttonText     "Replace <> with []"' in gui
    assert "?defValue       t" in gui
    assert gui.count("inst->optionsLay") == 3
    assert gui.count('?f "Options"') == 1
    assert 'SICO_tomlWriteSection(outFile "options")' in callback
    assert (
        'SICO_tomlWriteBool(outFile "replace_bus_bit_char"'
        in callback
    )
    assert "form~>replaceBusBitChar~>value" in callback
