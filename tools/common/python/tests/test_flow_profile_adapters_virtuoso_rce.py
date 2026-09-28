from __future__ import annotations

from flow_profile_adapter_fixtures import *

@pytest.mark.skipif(not RUN_SKILL_PROBE, reason="set RCE_RUN_SKILL_PROBE=1")
def test_rce_profile_applies_and_rolls_back_with_virtuoso(tmp_path: Path) -> None:
    virtuoso = shutil.which(os.environ.get("RCE_VIRTUOSO", "virtuoso"))
    if virtuoso is None:
        pytest.skip("virtuoso is unavailable")
    install = tmp_path / "install"
    install.mkdir()
    (install / "tools").symlink_to(CAD_ROOT, target_is_directory=True)
    tech = tmp_path / "tech/typ"
    tech.mkdir(parents=True)
    (tech / "qrcTechFile").write_text("", encoding="utf-8")
    nets = tmp_path / "nets.list"
    nets.write_text("VDD\n", encoding="utf-8")
    profile = tmp_path / "rce-profile.toml"
    profile.write_text("profile\n", encoding="utf-8")
    replay = tmp_path / "rce-profile.il"
    log = tmp_path / "rce-profile.log"
    replay.write_text(
        "\n".join(
            (
                f'setShellEnvVar("CAD_HOME" "{install}")',
                f'load("{install}/tools/rce/skill++/RCE.ils")',
                "procedure(SICO_profileMessage(severity flow message) "
                'printf("PROFILE_MESSAGE:%s:%s\\n" flow message) nil)',
                "cadDisplayRceForm()",
                "form=rceForm",
                "data=list(",
                f'  list("profile.path" "{profile}")',
                '  list("run.root_type" "Customize Directory")',
                f'  list("run.root" "{tmp_path}/runs")',
                '  list("input.type" "OA")',
                '  list("extract.tool" "QRC")',
                '  list("extract.tech_name" "typ")',
                f'  list("extract.tech_dir" "{tmp_path}/tech")',
                '  list("extract.corner_scope" "Single Corner")',
                '  list("extract.corners" list("typ"))',
                '  list("extract.corner_temperatures" list("125"))',
                '  list("extract.rc_type" "R+Cg+Cc")',
                '  list("extract.output_type" "smartview")',
                '  list("extract.view.kind" "Extracted View")',
                '  list("extract.view.name" "av_extracted")',
                '  list("reduction.enabled" t)',
                '  list("reduction.mode" "Selection File")',
                '  list("reduction.output_tag" "profiled")',
                '  list("reduction.temperature" "85")',
                '  list("reduction.selection_file" "nets.list")',
                f'  list("reduction.selection_file.resolved" "{nets}")',
                '  list("selection.net_enable" t)',
                '  list("selection.nets" "nets.list")',
                '  list("selection.cell_enable" t)',
                '  list("selection.cells" "blockA blockB"))',
                'applyOk=SICO_profileApply(form "RCE" data)',
                "successOk=and(applyOk "
                'form~>cornerType~>value=="Single Corner" '
                'form~>outType~>value=="view" '
                'form~>reductionEnable~>value '
                'form~>reductionMode~>value=="Selection File" '
                'form~>reductionOutputTag~>value=="profiled" '
                'form~>reductionTemperature~>value=="85" '
                'form~>viewKind~>value=="Smart View" '
                f'form~>reductionSelectionFile~>value=="{nets}" '
                f'form~>netSelFile~>value=="{nets}" '
                'form~>cellSelFile~>value=="blockA blockB")',
                'before=SICO_profileCollect(form "RCE")',
                'badData=cons(list("extract.output_type" "unsupported") '
                'setof(entry before car(entry)!="extract.output_type"))',
                'badOk=!SICO_profileApply(form "RCE" badData)',
                'rollbackOk=and(badOk form~>outType~>value=="view" '
                'form~>reductionEnable~>value '
                'form~>reductionMode~>value=="Selection File" '
                'form~>reductionOutputTag~>value=="profiled" '
                'form~>reductionTemperature~>value=="85" '
                'form~>viewKind~>value=="Smart View" '
                f'form~>reductionSelectionFile~>value=="{nets}" '
                f'form~>netSelFile~>value=="{nets}" '
                'form~>cellSelFile~>value=="blockA blockB")',
                "when(and(successOk rollbackOk)",
                '  printf("RCE_REAL_PROFILE_OK\\n"))',
                "when(form hiFormDone(form) hiDeleteForm(form))",
                "exit()",
                "",
            )
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "RCE_DB_DIR": str(tmp_path),
            "RCE_LVS_FILE": "default,/tmp/rules.lvs",
            "QUANTUS_TECH_DIR": f"typ,{tmp_path}/tech",
            "RCE_DEF_TOOL": "QRC",
            "RCE_OUTPUT_CHOICES": "dspf,view",
        }
    )
    completed = subprocess.run(
        [virtuoso, "-nograph", "-nocdsinit", "-replay", str(replay), "-log", str(log)],
        text=True,
        capture_output=True,
        timeout=80,
        check=False,
        env=environment,
    )
    output = completed.stdout + completed.stderr
    if log.is_file():
        output += log.read_text(encoding="utf-8", errors="replace")
    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "(reader)" not in output
    assert "Bad value" not in output
    assert "\\o RCE_REAL_PROFILE_OK" in output
