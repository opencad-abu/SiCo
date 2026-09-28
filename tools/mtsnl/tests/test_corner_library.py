from dataclasses import replace
from pathlib import Path
import json

import pytest

from mtsnetlistor.model import (NetlistRequest, SourceDesign, ModelEntry, ProcessOptions,
                                CornerExport, CornerProfile, TargetSelection,
                                MTS_DESIGN_CIRCUIT_SECTION)
from mtsnetlistor.config import (save_request, load_request, canonical_request_digest,
                                WorkspaceConfig, WorkspaceProcess, save_workspace, load_workspace)
from mtsnetlistor.corner_library import (assemble_library, split_library, validate_library,
                                        rename_library, inherit_temperature)
from mtsnetlistor.scoper import scope_netlist
from mtsnetlistor.scoper.lexer import ScopeError
from mtsnetlistor.errors import RequestValidationError, MtsNetlistorError
from mtsnetlistor import workflow, publish, generation_workflow
from mtsnetlistor.binding import _render_binding_script
from mtsnetlistor.artifacts import sha256_file


def setup(tmp_path):
    cds = tmp_path / 'cds.lib'
    cds.write_text('// private\n')
    mos = tmp_path / 'mos.scs'
    mos.write_text('section tt\nendsection tt\n')
    cap = tmp_path / 'cap.scs'
    cap.write_text('section cap_nom\nendsection cap_nom\n')
    profiles = tuple(CornerProfile(c, (ModelEntry(mos, f'mos_{c}'), ModelEntry(cap, 'cap_nom')))
                     for c in ('tt', 'ss', 'ff'))
    return NetlistRequest(SourceDesign(cds, 'src', 'core'),
                         corner_export=CornerExport('library', 'mts_pdkA_corner', profiles),
                         temperature_mode='inherit').validate()


def blocks(request):
    return [scope_netlist('\n'.join([
        'simulator lang=spectre',
        *(f'include "{m.file}" section={m.section}' for m in p.models),
        'subckt child (a b)', 'R (a b) resistor r=1k', 'ends child',
        'subckt core (a b)', 'X (a b) child', 'ends core',
    ]), 'spectre', 'core') for p in request.corner_export.profiles]


def test_request_workspace_roundtrip_and_digest(tmp_path):
    request = setup(tmp_path)
    save_request(request, tmp_path/'one.toml')
    assert load_request(tmp_path/'one.toml') == request
    spec = replace(request.selected_cells[0], cell='second')
    multi = replace(request, cell_specs=(request.selected_cells[0], spec)).validate()
    save_request(multi, tmp_path/'many.toml')
    assert load_request(tmp_path/'many.toml') == multi
    workspace = WorkspaceConfig((WorkspaceProcess('PDK A', multi), WorkspaceProcess('PDK B', request)))
    save_workspace(workspace, tmp_path/'work.toml')
    assert load_workspace(tmp_path/'work.toml') == workspace.validate()
    assert canonical_request_digest(request) != canonical_request_digest(replace(request, temperature_mode='fixed'))
    assert canonical_request_digest(request) != canonical_request_digest(replace(request, corner_export=replace(request.corner_export, profiles=tuple(reversed(request.corner_export.profiles)))))


def test_section_validation_scope_names_ports_models_and_rename(tmp_path):
    request = setup(tmp_path)
    text = assemble_library(request.corner_export, 'core', blocks(request))
    sections = split_library(text, 'core')
    assert [name for name, _ in sections] == ['tt', 'ss', 'ff']
    assert all(block.count('subckt child') == 1 for _, block in sections)
    assert text.count('subckt child') == 1
    assert text.count('section=__mts_design_circuit') == 3
    assert [block for _, block in sections] == [block.output for block in blocks(request)]
    assert text.count('section=cap_nom') == 3
    assert validate_library(text, request.corner_export, 'core') == ('a', 'b')
    renamed = rename_library(text, request.corner_export, 'core', 'IP')
    assert renamed.count('subckt IP') == 3 and 'subckt core' not in renamed
    assert renamed.count('include "IP_corners.scs" section=__mts_design_circuit') == 3
    assert '"core_corners.scs"' not in renamed
    assert renamed.count('subckt child') == 1
    assert validate_library(renamed, request.corner_export, 'IP') == ('a', 'b')
    for changed in (text.replace('endsection ss', 'endsection tt'),
                    text.replace('subckt core (a b)', 'subckt core (b a)', 1),
                    text.replace('section=mos_ss', 'section=mos_tt'),
                    text.replace('endlibrary core_corners', ''),
                    text.replace('endsection ff', 'endsection ff\ninclude "external"')):
        with pytest.raises((ScopeError, RequestValidationError)):
            validate_library(changed, request.corner_export, 'core')
    with pytest.raises(ScopeError):
        scope_netlist(text, 'spectre', 'core')


def expanded_library(request, scoped):
    return '\n'.join(['simulator lang=spectre', 'library core_corners',
                      *(f'section {p.name}\n{b.output}endsection {p.name}'
                        for p, b in zip(request.corner_export.profiles, scoped)),
                      'endlibrary core_corners', ''])


def test_existing_expanded_library_remains_valid_and_renames(tmp_path):
    request = setup(tmp_path)
    text = expanded_library(request, blocks(request))
    assert validate_library(text, request.corner_export, 'core') == ('a', 'b')
    renamed = rename_library(text, request.corner_export, 'core', 'IP')
    assert renamed.count('subckt child') == 3
    assert MTS_DESIGN_CIRCUIT_SECTION not in renamed
    assert validate_library(renamed, request.corner_export, 'IP') == ('a', 'b')


@pytest.mark.parametrize('change', [
    lambda b: b.replace('r=1k', 'r=2k'),
    lambda b: b.replace('ends child', 'late options temp=75\nends child'),
    lambda b: b.replace('ends child', 'include "nested.scs"\nends child'),
    lambda b: b.replace('ends core', 'late options temp=75\nends core'),
])
def test_different_or_interleaved_bodies_remain_expanded(tmp_path, change):
    request = setup(tmp_path)
    scoped = blocks(request)
    scoped[1] = replace(scoped[1], output=change(scoped[1].output))
    text = assemble_library(request.corner_export, 'core', scoped)
    assert MTS_DESIGN_CIRCUIT_SECTION not in text
    assert [block for _, block in split_library(text, 'core')] == [b.output for b in scoped]


def test_single_corner_keeps_complete_block(tmp_path):
    request = setup(tmp_path)
    export = replace(request.corner_export, profiles=request.corner_export.profiles[:1])
    text = assemble_library(export, 'core', blocks(request)[:1])
    assert MTS_DESIGN_CIRCUIT_SECTION not in text
    assert split_library(text, 'core')[0][1] == blocks(request)[0].output


def test_shared_circuit_keeps_bus_ports_parameters_options_and_model_order(tmp_path):
    request = setup(tmp_path)
    scoped = []
    for index, b in enumerate(blocks(request)):
        output = b.output.replace('subckt core (a b)',
            'subckt core (A\\<1\\> \\\n    A\\<0\\>)\nscopedOptions options temp=' + str(27 + index) + ' \\\n    tnom=30 scale=1')
        output = output.replace('subckt child', 'parameters gain=2\nsubckt child')
        scoped.append(replace(b, output=output))
    text = assemble_library(request.corner_export, 'core', scoped)
    assert text.count('parameters gain=2') == 1
    assert text.count('scopedOptions options temp=') == 3
    assert [block for _, block in split_library(text, 'core')] == [b.output for b in scoped]
    assert validate_library(text, request.corner_export, 'core') == ('A\\<1\\>', 'A\\<0\\>')
    inherited = assemble_library(request.corner_export, 'core', [inherit_temperature(b) for b in scoped])
    assert 'temp=' not in inherited
    assert inherited.count('tnom=30') == 3
    assert inherited.count('subckt child') == 1
    renamed = rename_library(text, request.corner_export, 'core', 'IP')
    assert validate_library(renamed, request.corner_export, 'IP') == ('A\\<1\\>', 'A\\<0\\>')


@pytest.mark.parametrize('change', [
    lambda t: t[:t.index('section __mts_design_circuit\n')] + 'endlibrary core_corners\n',
    lambda t: t.replace('include "core_corners.scs" section=__mts_design_circuit', '', 1),
    lambda t: t.replace('section=__mts_design_circuit', 'section=tt', 1),
    lambda t: t.replace('"core_corners.scs"', '"stale_corners.scs"', 1),
    lambda t: t.replace('section=__mts_design_circuit',
                       'section=__mts_design_circuit\ninclude "core_corners.scs" section=__mts_design_circuit', 1),
    lambda t: t.replace('subckt child (a b)', 'include "core_corners.scs" section=__mts_design_circuit\nsubckt child (a b)'),
    lambda t: t.replace('subckt child (a b)', 'include "core_corners.scs" section=ss\nsubckt child (a b)'),
    lambda t: t.replace('subckt child (a b)', 'subckt core (a b)'),
    lambda t: t.replace('endlibrary core_corners',
                       'section __mts_design_circuit\nR (a b) resistor r=1k\nendsection __mts_design_circuit\nendlibrary core_corners'),
    lambda t: t.replace('include "core_corners.scs" section=__mts_design_circuit\nends core',
                       'ends core\ninclude "core_corners.scs" section=__mts_design_circuit', 1),
    lambda t: t.replace('include "core_corners.scs" section=__mts_design_circuit',
                       'subckt inner (a b)\ninclude "core_corners.scs" section=__mts_design_circuit\nends inner', 1),
])
def test_invalid_design_circuit_references_rejected(tmp_path, change):
    request = setup(tmp_path)
    text = assemble_library(request.corner_export, 'core', blocks(request))
    with pytest.raises((ScopeError, RequestValidationError)):
        validate_library(change(text), request.corner_export, 'core')


def test_inherit_temperature_filters_options_but_not_tnom_or_parameters():
    text = '''simulator lang=spectre
subckt core (a b)
simulatorOptions options temp=27 \\
  tnom=30 reltol=1e-5
parameters temp_scale=2
custom options temp=75
R (a b) resistor r=temp_scale
ends core
'''
    scoped = scope_netlist(text, 'spectre', 'core')
    inherited = inherit_temperature(scoped)
    assert 'temp=' not in inherited.output
    assert 'tnom=30' in inherited.output and 'temp_scale=2' in inherited.output
    assert 'custom options' not in inherited.output
    assert len([d for d in inherited.dropped if 'temperature' in d]) == 2
    assert 'temp=27' in scoped.output
    assert inherit_temperature(inherited).output == inherited.output


def test_binding_script_rebuilds_existing_base_and_user_cdf_on_explicit_overwrite(
    tmp_path, protected_worker_context,
):
    report = tmp_path / "binding.report"
    call = _render_binding_script("target", "IP", ("d", "g", "s", "b"), True, report)
    assert 'mtsRuntimeBinding("target" "IP" list("d" "g" "s" "b") t' in call
    script = (Path(__file__).resolve().parents[1] / "skill/MTS_bindingWorker.il").read_text()
    assert "cdfGetUserCellCDF" in script
    assert "cdfFindParamByName(mtsUserCDF \"model\")" in script
    assert "foreach(mtsOldCDF list(mtsCDF mtsUserCDF)" in script
    assert "cdfDeleteCDF(mtsOldCDF)" in script
    assert "dbCopyCellView(mtsSym mtsLib mtsCell \"spectre\" nil nil t)" in script
    assert "view_source=%s" in script
    assert "minimal-pins" in script
    # A non-overwrite worker still refuses CDF state before opening the view.
    reject = _render_binding_script("target", "IP", ("d", "g", "s", "b"), False, report)
    assert 'mtsRuntimeBinding("target" "IP" list("d" "g" "s" "b") nil' in reject
    assert 'when((mtsCDF || mtsUserCDF) && !overwrite' in script


def test_library_publication_conflict_is_rejected_before_binding_worker(tmp_path, monkeypatch):
    request = setup(tmp_path)
    target = tmp_path / "target-lib"
    target.mkdir()
    target_cds = tmp_path / "target.cds.lib"
    target_cds.write_text(f"DEFINE target {target}\n", encoding="utf-8")
    session = publish.SessionDescriptor(
        1, "test", target_cds,
        sha256_file(target_cds),
        {"target": str(target)},
    )
    text = assemble_library(request.corner_export, "core", blocks(request))
    netlist = tmp_path / "core_corners.scs"
    netlist.write_text(text, encoding="utf-8")
    conflicting = target / "IP" / "spectreText"
    conflicting.mkdir(parents=True)
    called = False

    def fail_worker(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("binding worker must not start after spectreText conflict")

    monkeypatch.setattr("mtsnetlistor.binding.run_isolated", fail_worker)
    target_request = replace(request, target=TargetSelection("target", "IP", False, True)).validate()
    with pytest.raises(RequestValidationError, match="spectreText"):
        publish.publish_text_view(target_request, session, netlist=netlist, run_dir=tmp_path / "run")
    assert called is False


def test_library_bundle_failure_reports_binding_view_and_data_dm_for_cleanup(
    tmp_path, monkeypatch, protected_worker_context,
):
    request = setup(tmp_path)
    target = tmp_path / "target-lib"
    target.mkdir()
    target_cds = tmp_path / "target.cds.lib"
    target_cds.write_text(f"DEFINE target {target}\n", encoding="utf-8")
    session = publish.SessionDescriptor(
        1, "test", target_cds,
        sha256_file(target_cds),
        {"target": str(target)},
    )
    netlist = tmp_path / "core_corners.scs"
    netlist.write_text(assemble_library(request.corner_export, "core", blocks(request)), encoding="utf-8")
    target_request = replace(request, target=TargetSelection("target", "IP", False, True)).validate()

    def fail_worker(*args, **kwargs):
        raise MtsNetlistorError("injected binding worker failure")

    monkeypatch.setattr("mtsnetlistor.binding.run_isolated", fail_worker)
    result = publish.publish_bundle(
        target_request, session, netlist=netlist, run_dir=tmp_path / "run"
    )
    assert result.status == "manual_cleanup_required"
    assert result.affected_views == (
        str(target / "IP" / "spectre"),
        str(target / "IP" / "data.dm"),
    )
    assert "injected binding worker failure" in result.message
    assert any("model-binding.log" in path for path in result.log_files)


@pytest.mark.parametrize('change', [
    lambda r: replace(r, dialect='hspiceD'),
    lambda r: replace(r, process_options=ProcessOptions(temp=27)),
    lambda r: replace(r, corner_export=replace(r.corner_export, profiles=())),
    lambda r: replace(r, corner_export=replace(r.corner_export, profiles=(r.corner_export.profiles[0],)*2)),
    lambda r: replace(r, corner_export=replace(r.corner_export, variable='VAR("bad")')),
    lambda r: replace(r, corner_export=replace(r.corner_export, profiles=(CornerProfile('tt',()),))),
    lambda r: replace(r, corner_export=replace(r.corner_export,
        profiles=(replace(r.corner_export.profiles[0], name=MTS_DESIGN_CIRCUIT_SECTION),))),
])
def test_bad_configuration_rejected(tmp_path, change):
    with pytest.raises(RequestValidationError):
        change(setup(tmp_path)).validate()


def test_inherited_temperature_requires_corner_library_mode(tmp_path):
    cds = tmp_path / "cds.lib"
    cds.write_text("// private\n")
    request = NetlistRequest(
        SourceDesign(cds, "src", "core"), temperature_mode="inherit"
    )
    with pytest.raises(RequestValidationError, match="corner library"):
        request.validate()


def test_each_corner_generated_and_failure_preserves_stable(tmp_path, monkeypatch):
    request = setup(tmp_path)
    # Fake the real worker boundary only: inspect the generated script and write
    # a different raw deck for its explicit model list.
    calls = []
    real = generation_workflow.run_isolated
    worker = tmp_path/'fake-ocean'
    worker.write_text('''#!/usr/bin/env python3
import sys,re
from pathlib import Path
script=Path(sys.argv[sys.argv.index('-replay')+1])
s=script.read_text()
includes=[]
for path,section in re.findall(r"'\\(\\\"([^\\\"]+)\\\" \\\"([^\\\"]+)\\\"\\)",s):
 includes.append(f'include "{path}" section={section}')
p=script.parent/'input.scs'
p.write_text('simulator lang=spectre\\n'+'\\n'.join(includes)+'\\nsubckt core (a b)\\nR (a b) resistor r=1k\\nends core\\n')
print('MTS_RAW_NETLIST='+str(p))
''')
    worker.chmod(0o755)
    # Sentinel spelling follows production renderer.
    worker.write_text(worker.read_text().replace('MTS_RAW_NETLIST=', 'MTS_NETLISTOR_RAW='))
    env = {'PATH': '/usr/bin:/bin', 'PROJ_ADE_DB_DIR': str(tmp_path/'ade')}
    result = workflow.generate(request, environ=env, ocean=str(worker))
    before = result.stable_output.read_bytes()
    publish.validate_run_artifact(request, result.stable_output, result.run_dir, strict_ownership=True)
    assert validate_library(before.decode(), request.corner_export, 'core') == ('a','b')
    manifest = json.loads((result.run_dir/'manifest.json').read_text())
    assert len(manifest['processes']) == 3
    def fail_second(*args, **kwargs):
        calls.append(kwargs['cwd'])
        if len(calls) == 2:
            raise MtsNetlistorError('injected second-corner failure')
        return real(*args, **kwargs)
    monkeypatch.setattr(generation_workflow, 'run_isolated', fail_second)
    with pytest.raises(MtsNetlistorError, match='second-corner'):
        workflow.generate(request, environ=env, ocean=str(worker))
    assert result.stable_output.read_bytes() == before
    monkeypatch.setattr(generation_workflow, 'run_isolated', real)
    recovered = workflow.generate(request, environ=env, ocean=str(worker))
    assert recovered.status == 'succeeded'
