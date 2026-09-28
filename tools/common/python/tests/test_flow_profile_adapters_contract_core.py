from __future__ import annotations

from flow_profile_adapter_fixtures import *

@pytest.mark.parametrize(
    ("relative", "collect", "expected"),
    (
        ("drc/skill++/DRCPROFILE.ils", "drcProfileCollect", DRC_FIELDS),
        ("lvs/skill++/LVSPROFILE.ils", "lvsProfileCollect", LVS_FIELDS),
        ("rce/skill++/RCEPROFILE.ils", "rceProfileCollect", RCE_FIELDS),
    ),
)
def test_profile_collectors_cover_business_state_only(
    relative: str, collect: str, expected: set[str]
) -> None:
    source = _source(relative)
    body = _procedure(source, collect)
    if collect == "rceProfileCollect":
        body = _procedure(source, "rceProfileFieldSpecs") + body

    assert _collect_paths(body) == expected
    for excluded in (
        "run.run_dir",
        "run.cds_lib",
        "input.layout.layer_map",
        "profile.path",
        "cadBatchEditIndex",
        "cadLsfDiscoveryCid",
        "cadLsfQueueDiscoveryCid",
        "drcRuleSelectCid",
        "drcRuleSelectRequestToken",
    ):
        assert excluded not in body


def test_drc_profile_apply_refreshes_dependencies_in_order() -> None:
    body = _procedure(_source("drc/skill++/DRCPROFILE.ils"), "drcProfileApply")

    _assert_order(
        body,
        "form~>outDirType~>value=rootType",
        "drcOutDirCB(form)",
        'when(form~>outDirType~>value=="Customize Directory"',
        'form~>outDirPath~>value=or(root "")',
    )
    _assert_order(
        body,
        "drcRuleSelectCancelDiscovery(form)",
        "form~>drcRunsetName~>value=SICO_profileDataValue(",
        "drcRunsetCB(form)",
        "form~>drcRunsetFile~>value=or(runsetFile",
        "drcRuleSelectInvalidate(form)",
        "form~>drcRuleSelectGroups=SICO_profileStringList(",
        "form~>drcRuleSelectRuleFile=SICO_absolutePath(",
        "drcRuleSelectEnableCB(form)",
    )
    _assert_order(
        body,
        'form~>runCpu~>value=SICO_profileDataValue(data "runtime.cpus"',
        "drcRunModeCB(form)",
    )
    _assert_order(
        body,
        "form~>customSvrfCommand~>value=SICO_profileDataValue(",
        "cadCustomSvrfCB(form)",
    )
    _assert_order(
        body,
        "form~>cadBatchTasks=SICO_profileDataValue(",
        "cadBatchRefreshReport(form)",
        "form~>runType~>value=SICO_profileDataValue(",
        "SICO_lsfSetPreferredValues(",
        "SICO_lsfChoicesCB(form)",
    )


def test_lvs_profile_apply_refreshes_dependencies_in_order() -> None:
    body = _procedure(_source("lvs/skill++/LVSPROFILE.ils"), "lvsProfileApply")

    _assert_order(
        body,
        'scope=SICO_profileDataValue(data "batch.scope"',
        'when(and(scope=="Multiple Cells" inputType!="OA")',
        'form~>batchRunScope~>value="Single Cell"',
    )
    _assert_order(
        body,
        "form~>outDirType~>value=rootType",
        "lvsOutDirCB(form)",
        'when(form~>outDirType~>value=="Customize Directory"',
        'form~>outDirPath~>value=or(root "")',
    )
    _assert_order(
        body,
        "form~>inpType~>value=inputType",
        "form~>gdsCell~>value=SICO_profileDataValue(",
        "lvsInpCB(form)",
        "cadCdlIncludeCB(form)",
    )
    _assert_order(
        body,
        "form~>lvsRunsetName~>value=SICO_profileDataValue(",
        "lvsRunsetCB(form)",
        "form~>lvsRunsetFile~>value=or(runsetFile",
    )
    _assert_order(
        body,
        "form~>lvsHcellFile~>value=SICO_profilePathValue(",
        "lvsRunModeCB(form)",
        "lvsHcellCB(form)",
    )
    _assert_order(
        body,
        "form~>lvsVirtualConnName~>value=SICO_profileDataValue(",
        "lvsVirtualConnCB(form)",
        "form~>customSvrfEnable~>value=SICO_profileDataValue(",
        "cadCustomSvrfCB(form)",
    )
    _assert_order(
        body,
        "form~>cadBatchTasks=SICO_profileDataValue(",
        "cadBatchRefreshReport(form)",
        "form~>runType~>value=SICO_profileDataValue(",
        "SICO_lsfSetPreferredValues(",
        "SICO_lsfChoicesCB(form)",
    )
    assert "lvsIgnoreErrorCB(form)" not in body


def test_rce_profile_apply_restores_state_after_dependency_callbacks() -> None:
    body = _procedure(_source("rce/skill++/RCEPROFILE.ils"), "rceProfileApply")

    _assert_order(
        body,
        "savedSelection=list(",
        'form~>batchRunScope~>value="Single Cell"',
        "form~>inpType~>value=inputType",
        "rceInpCB(form)",
        "rceExtToolCB(form)",
        "rceProfileRestoreCorners(form corners temperatures cornerScope)",
        "rceRcTypeCB(form)",
        "form~>viewKind~>value=savedViewKind",
        "rceOutTypeCB(form)",
        "form~>netSelBtn~>value=nth(0 savedSelection)",
        "rceSyncNetlistCustomize(form)",
        "form~>cadBatchTasks=SICO_profileDataValue(",
        "SICO_lsfSetPreferredValues(",
        "SICO_lsfChoicesCB(form)",
    )
    assert 'form~>startRve~>value=savedStartRve' in body
    assert 'form~>nlRmInst~>value=nth(7 savedNetlist)' in body
    assert 'form~>viewDeviceMap~>value=if(form~>extTool~>value=="CalXRC"' in body
    assert "unless(rceProfileRestoreCorners(form corners temperatures cornerScope)" in body

    rc_type = _procedure(_source("rce/skill++/RCECB.ils"), "rceRcTypeCB")
    assert 'form~>outType~>value="sp"' not in rc_type
    assert "member(form~>outType~>value form~>outType~>choices)" in rc_type


def test_rce_profile_rejects_missing_saved_corners_and_separates_view_maps() -> None:
    source = _source("rce/skill++/RCEPROFILE.ils")
    restore = _procedure(source, "rceProfileRestoreCorners")
    collect = _procedure(source, "rceProfileCollect")
    choice = _procedure(source, "rceProfileSetChoice")
    selection = _procedure(source, "rceProfileSelectionValue")

    assert "available=or(form~>rceAvailableCorners nil)" in restore
    assert "matched=rceMatchingProcessCorner(requested available)" in restore
    assert "unless(matched return(nil))" in restore
    assert 'if(form~>extTool~>value=="CalXRC"' in collect
    assert 'list("extract.view.cellmap_file"' in collect
    assert 'list("extract.view.device_mapping_file"' in collect
    assert "procedure(rceProfileOutputViewKind" in source
    assert '("smartview" "Smart View")' in source
    assert '("extview" "Extracted View")' in source
    assert "field~>choices=" not in choice
    assert 'member("view" choices)' in choice
    assert "field~>value=choice" in choice
    assert "isFile(resolved)" in selection
    assert 'SICO_profileDataValue(data "profile.path" nil)' in selection
    assert "if(and(candidate isFile(candidate)) then candidate else value)" in selection
    assert 'rceProfileSelectionValue(data "selection.nets")' in source
    assert 'rceProfileSelectionValue(data "selection.cells")' in source


def test_rce_profile_helpers_preserve_choice_path_and_corner_contracts_with_dbaccess(
    tmp_path: Path,
) -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")

    profile = tmp_path / "profile.toml"
    profile.write_text("profile\n", encoding="utf-8")
    nets = tmp_path / "nets.list"
    nets.write_text("VDD\n", encoding="utf-8")
    sources = (
        CAD_ROOT / "common/skill/SICO_toml.il",
        CAD_ROOT / "common/skill/SICO_profile.il",
        CAD_ROOT / "rce/skill++/RCEPROFILE.ils",
    )
    skill = "\n".join(
        (
            *(f'load("{source}")' for source in sources),
            "defstruct(rceProfileProbeField value choices)",
            "defstruct(rceProfileProbeForm cornerType corner temp "
            "rceAvailableCorners rceCornerNameFields "
            "rceCornerSelectFields rceCornerTempFields)",
            "procedure(rceMatchingProcessCorner(name corners) "
            "let((match) foreach(corner corners "
            "when(lowerCase(name)==lowerCase(corner) match=corner)) match))",
            "procedure(rceMatchingCornerRecord(name records) "
            "let((match) foreach(record records "
            "when(lowerCase(name)==lowerCase(car(record)) match=record)) match))",
            "procedure(rceRefreshProcessCorners(form) t)",
            "procedure(rceCornerTypeCB(form) t)",
            'name=make_rceProfileProbeField(?value "typ")',
            "select=make_rceProfileProbeField(?value list(nil))",
            'temperature=make_rceProfileProbeField(?value "25")',
            "form=make_rceProfileProbeForm("
            '?cornerType make_rceProfileProbeField(?value "Single Corner") '
            '?corner make_rceProfileProbeField(?value "typ") '
            '?temp make_rceProfileProbeField(?value "25") '
            '?rceAvailableCorners list("typ") '
            "?rceCornerNameFields list(name) "
            "?rceCornerSelectFields list(select) "
            "?rceCornerTempFields list(temperature))",
            "choice=make_rceProfileProbeField("
            '?value "dspf" ?choices list("dspf" "view"))',
            "choiceBefore=copy(choice~>choices)",
            'choiceOk=and(rceProfileSetChoice(choice "view") '
            'choice~>value=="view" equal(choice~>choices choiceBefore))',
            'legacyChoiceOk=and(rceProfileSetChoice(choice "smartview") '
            'choice~>value=="view" equal(choice~>choices choiceBefore))',
            'smartKindOk=rceProfileOutputViewKind("smartview" '
            '"Extracted View")=="Smart View"',
            'extractedKindOk=rceProfileOutputViewKind("extview" '
            '"Smart View")=="Extracted View"',
            'canonicalKindOk=rceProfileOutputViewKind("view" '
            '"extracted")=="Extracted View"',
            'choiceBad=and(!rceProfileSetChoice(choice "unsupported") '
            'choice~>value=="view" equal(choice~>choices choiceBefore))',
            'cornerOk=and(rceProfileRestoreCorners(form list("typ") '
            'list("125") "Multiple Corners") '
            'form~>cornerType~>value=="Multiple Corners" '
            'car(select~>value) temperature~>value=="125")',
            'fileOk=rceProfileSelectionValue(list('
            f'list("profile.path" "{profile}") '
            'list("selection.nets" "nets.list")) '
            '"selection.nets")=='
            f'"{nets}"',
            'resolvedOk=rceProfileSelectionValue(list('
            'list("selection.nets" "inline") '
            f'list("selection.nets.resolved" "{nets}")) '
            '"selection.nets")=='
            f'"{nets}"',
            'inlineOk=rceProfileSelectionValue(list('
            f'list("profile.path" "{profile}") '
            'list("selection.cells" "blockA blockB") '
            'list("selection.cells.resolved" "/missing/cells.list")) '
            '"selection.cells")=="blockA blockB"',
            "when(and(choiceOk legacyChoiceOk smartKindOk extractedKindOk "
            "canonicalKindOk choiceBad cornerOk fileOk resolvedOk inlineOk)",
            '  printf("RCE_PROFILE_HELPERS_OK\\n"))',
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess],
        input=skill,
        text=True,
        capture_output=True,
        timeout=40,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "PARSER WARNING" not in output
    assert "illegal left hand side" not in output
    assert "(reader)" not in output
    assert "RCE_PROFILE_HELPERS_OK" in output


def test_rce_collector_has_unique_derived_fields_and_keeps_radio_choices_fixed() -> None:
    source = _source("rce/skill++/RCEPROFILE.ils")
    collector = _procedure(source, "rceProfileFieldSpecs") + _procedure(
        source, "rceProfileCollect"
    )
    set_choice = _procedure(source, "rceProfileSetChoice")

    assert collector.count('"extract.output_type"') == 1
    assert collector.count('"extract.view.kind"') == 1
    assert "setcar(cdr(serverEntry) SICO_lsfSelectedHost(cadr(serverEntry)))" in collector
    assert "cadr(serverEntry)=" not in collector
    assert "field~>choices=" not in source
    assert "member(value choices)" in set_choice
    assert "field~>value=choice" in set_choice
    assert "t)" in set_choice


def test_rce_collector_normalizes_auto_host_with_dbaccess() -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    sources = (
        CAD_ROOT / "common/skill/SICO_toml.il",
        CAD_ROOT / "common/skill/SICO_profile.il",
        CAD_ROOT / "rce/skill++/RCEPROFILE.ils",
    )
    skill = "\n".join(
        (
            *(f'load("{source}")' for source in sources),
            "defstruct(rceCollectorField value)",
            "defstruct(rceCollectorForm srvName lvsVirtualConn extTool "
            "viewKind viewKindFixed cdlIncludeBtn outType relFilterCapBtn "
            "absFilterCapBtn absFilterResBtn nlParInfo cadBatchTasks "
            "viewDeviceMap)",
            'procedure(SICO_lsfSelectedHost(host) if(host=="Auto (LSF Scheduler)" '
            'then "" else host))',
            "procedure(SICO_profileCollectFields(form specs) "
            'list(list("run.server_name" form~>srvName~>value)))',
            "procedure(rceSelectedProcessCorners(form) nil)",
            "procedure(rceSelectedCornerTemperatures(form) nil)",
            "form=make_rceCollectorForm(",
            '  ?srvName make_rceCollectorField(?value "Auto (LSF Scheduler)")',
            "  ?lvsVirtualConn make_rceCollectorField(?value list(nil t))",
            '  ?extTool make_rceCollectorField(?value "QRC")',
            '  ?viewKind make_rceCollectorField(?value "Smart View")',
            '  ?viewKindFixed make_rceCollectorField(?value "Plain View")',
            "  ?cdlIncludeBtn make_rceCollectorField(?value nil)",
            '  ?outType make_rceCollectorField(?value "dspf")',
            "  ?relFilterCapBtn make_rceCollectorField(?value nil)",
            "  ?absFilterCapBtn make_rceCollectorField(?value nil)",
            "  ?absFilterResBtn make_rceCollectorField(?value nil)",
            "  ?nlParInfo make_rceCollectorField(?value list(nil nil nil))",
            '  ?viewDeviceMap make_rceCollectorField(?value ""))',
            "autoEntries=rceProfileCollect(form)",
            'autoOk=equal(assoc("run.server_name" autoEntries) '
            'list("run.server_name" ""))',
            'form~>srvName~>value="node01"',
            "hostEntries=rceProfileCollect(form)",
            'hostOk=equal(assoc("run.server_name" hostEntries) '
            'list("run.server_name" "node01"))',
            'when(and(autoOk hostOk) printf("RCE_PROFILE_HOST_NORMALIZE_OK\\n"))',
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess],
        input=skill,
        text=True,
        capture_output=True,
        timeout=40,
        check=False,
    )
    output = completed.stdout + completed.stderr

    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "PARSER WARNING" not in output
    assert "illegal left hand side" not in output
    assert "(reader)" not in output
    assert "RCE_PROFILE_HOST_NORMALIZE_OK" in output
