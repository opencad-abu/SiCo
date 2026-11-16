"""B0 capability audit: explicit saved OA reads, no foreground handle reuse."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cadai.skill_check import check_source

CAD = Path(__file__).resolve().parents[3]
IC = Path('/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/virtuoso')
DB = IC.with_name('dbAccess')
SOURCES = ('AI_protocol.il', 'AI_eval.il', 'AI_inspect_values.il', 'AI_inspect_records.il', 'AI_inspect_snapshot.il', 'AI_inspect.il', 'AI_background_probe.il')
TARGET = dict(lib='B0Work', cell='top', view_name='schematic',
              oa_view_type='schematic')


def environment(directory, *, graphical=False):
    env = {k: v for k, v in os.environ.items()
           if k not in {'LD_LIBRARY_PATH', 'LD_PRELOAD', 'PYTHONPATH', 'PYTHONHOME'}
           and not k.startswith(('QT_', 'CAD_', 'CDS_MPS_'))}
    env['PWD'] = str(directory)
    if not graphical:
        env.pop('DISPLAY', None)
        env.pop('WAYLAND_DISPLAY', None)
        env.pop('CDS_NOGRAPH_DISPLAY', None)
    return env


def loads():
    result = []
    for name in SOURCES:
        source = CAD / 'ai/skill' / name
        assert check_source(source.read_bytes(), source_name=name)['ok']
        result.append(f'load({json.dumps(str(source))})')
    return result


def run(directory, lines, *, cds_lib=None, binary=DB, timeout=30, prefix=()):
    if not binary.is_file():
        pytest.skip('IC231 unavailable')
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    if cds_lib is None:
        cds_lib = directory / 'cds.lib'
        cds_lib.write_text('')
    source = directory / 'probe.il'
    source.write_text('if(errset(progn(\n' + '\n'.join(lines + [
        'b0Port=outfile("passed") fprintf(b0Port "%s" getVersion(t)) close(b0Port) exit(0)',
    ]) + '\n) t) then exit(0) else exit(1))\n')
    command = [str(binary), '-nocdsinit', '-cdslib', str(cds_lib)]
    if binary == DB:
        command += ['-load', str(source)]
    else:
        command += ['-nograph', '-restore', str(source), '-log', str(directory / 'CDS.log')]
    result = subprocess.run(list(prefix) + command, cwd=directory, env=environment(directory),
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    output = result.stdout + result.stderr
    (directory / 'stdout.log').write_text(output)
    assert result.returncode == 0 and (directory / 'passed').exists(), output
    assert '*Error*' not in output and '(reader)' not in output, output
    return (directory / 'passed').read_text()


@pytest.fixture(scope='module')
def design(tmp_path_factory):
    root = tmp_path_factory.mktemp('b0-fixture')
    callback = 'progn(b0CallbackPort=outfile("callback-executed") close(b0CallbackPort) error("B0_CALLBACK_FORBIDDEN"))'
    run(root, [
        f'ddCreateLib("B0Tech" {json.dumps(str(root / "tech"))})',
        'b0Tech=techOpenTechFile("B0Tech" "tech.db" "w")',
        'unless(b0Tech error("fixture technology unavailable"))',
        'techCreateLayer(b0Tech 1 "B0Geometry") techSaveTechFile(b0Tech)',
        f'ddCreateLib("B0Devices" {json.dumps(str(root / "devices"))})',
        f'ddCreateLib("B0Work" {json.dumps(str(root / "work"))})',
        'techBindTechFile(ddGetObj("B0Devices") "B0Tech")',
        'techBindTechFile(ddGetObj("B0Work") "B0Tech")',
        'b0Master=dbOpenCellViewByType("B0Devices" "leaf" "symbol" "schematicSymbol" "w")',
        'b0PinNet=dbCreateNet(b0Master "P") b0Term=dbCreateTerm(b0PinNet "P" "inputOutput")',
        'dbCreateRect(b0Master list("B0Geometry" "drawing") list(0:0 1:1))',
        'b0Pin=dbCreateRect(b0Master list("B0Geometry" "drawing") list(0:0 0.1:0.1))',
        'dbCreatePin(b0PinNet b0Pin "P")',
        'dbSave(b0Master) dbClose(b0Master)',
        'b0Cdf=cdfCreateBaseCellCDF(ddGetObj("B0Devices" "leaf"))',
        'cdfCreateParam(b0Cdf ?name "w" ?prompt "Width" ?type "string" ?defValue "1u"'
        f' ?callback {json.dumps(callback)} ?display {json.dumps(callback)} ?editable "nil")',
        'cdfSaveCDF(b0Cdf)',
        'b0Master=dbOpenCellViewByType("B0Devices" "leaf" "symbol" "" "r")',
        'b0Top=dbOpenCellViewByType("B0Work" "top" "schematic" "schematic" "w")',
        'b0Inst=dbCreateInst(b0Top b0Master "I0" 0:0 "R0")',
        'b0Net=dbCreateNet(b0Top "SAVED")',
        'dbCreateInstTerm(b0Net b0Inst car(b0Master~>terminals))',
        'dbCreateTerm(b0Net "SAVED" "inputOutput")',
        'b0TopPin=dbCreateRect(b0Top list("B0Geometry" "drawing") list(2:0 2.1:0.1))',
        'dbCreatePin(b0Net b0TopPin "SAVED")',
        'dbSave(b0Top) dbClose(b0Top) dbClose(b0Master)',
        'b0Plain=dbOpenCellViewByType("B0Devices" "plain" "symbol" "schematicSymbol" "w")',
        'dbCreateRect(b0Plain list("B0Geometry" "drawing") list(0:0 1:1))',
        'dbSave(b0Plain)',
        'b0PlainTop=dbOpenCellViewByType("B0Work" "plain" "schematic" "schematic" "w")',
        'dbCreateInst(b0PlainTop b0Plain "I0" 0:0 "R0")',
        'dbSave(b0PlainTop) dbClose(b0PlainTop) dbClose(b0Plain)',
        'b0Leaf=dbOpenCellViewByType("B0Devices" "leaf" "symbol" "" "r")',
        'b0Nested=dbOpenCellViewByType("B0Devices" "nested" "symbol" "schematicSymbol" "w")',
        'dbCreateInst(b0Nested b0Leaf "child" 0:0 "R0") dbSave(b0Nested)',
        'b0NestedTop=dbOpenCellViewByType("B0Work" "nested" "schematic" "schematic" "w")',
        'dbCreateInst(b0NestedTop b0Nested "I0" 0:0 "R0")',
        'dbSave(b0NestedTop) dbClose(b0NestedTop) dbClose(b0Nested) dbClose(b0Leaf)',
    ], binary=IC)
    return root


def fingerprints(design):
    paths = [design / 'cds.lib']
    for name in ('tech', 'devices', 'work'):
        paths += [path for path in (design / name).rglob('*')
                  if path.is_file() and not any(word in path.name for word in ('.cdslck', '.lock'))]
    return {str(path.relative_to(design)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def probe(directory, design, *, target=None, before=(), after=(), binary=IC, bootstrap=None, prefix=()):
    from sico.service.background_capability import build_background_probe, assess_background_probe
    target = TARGET if target is None else target
    version = run(directory, (loads() if bootstrap is None else bootstrap) + list(before) + [
        'when(and(isCallable(\'hiGraphicMode) hiGraphicMode()) error("Reader is graphical"))',
        f'aiWriteTextFile("result.json" {build_background_probe("inspect_schematic", target)})',
    ] + list(after), cds_lib=design / 'cds.lib', binary=binary, prefix=prefix)
    assert not (directory / 'callback-executed').exists()
    raw = (directory / 'result.json').read_text()
    return assess_background_probe(raw, target=target, expected_version=version), json.loads(raw)


def test_explicit_read_has_symbol_cdf_technology_and_no_gui(design, tmp_path):
    before = fingerprints(design)
    result, raw = probe(tmp_path / 'reader', design)
    assert result['available'] and result['ok'], (result, raw)
    assert result['snapshot_scope'] == 'saved_oa' and not result['foreground_memory_visible']
    assert raw['open_mode'] == 'r' and raw['modified'] is False
    assert raw['technology_library'] == 'B0Tech'
    dependency = raw['dependencies'][0]
    assert dependency['master'] == dict(lib='B0Devices', cell='leaf', view='symbol')
    assert dependency['cdf_present'] and dependency['cdf_parameter_count'] == 1
    assert dependency['technology_library'] == 'B0Tech'
    assert raw['inspection']['instances'][0]['terminals'][0]['net'] == 'SAVED'
    assert fingerprints(design) == before


@pytest.mark.parametrize('failure, reason', [
    ('missing', 'cellview_open_failed'), ('oa_view_type', 'oa_view_type_mismatch'),
    ('held_writer', 'writable_handle'), ('inspector', 'inspection_failed'),
])
def test_capability_failures_are_explicit_and_do_not_modify_target(design, tmp_path, failure, reason):
    before = fingerprints(design)
    target, setup = dict(TARGET), []
    if failure == 'missing':
        target['cell'] = 'absent'
    elif failure == 'oa_view_type':
        target.update(lib='B0Devices', cell='leaf', view='symbol')
    elif failure == 'held_writer':
        setup = ['b0Held=dbOpenCellViewByType("B0Work" "top" "schematic" "schematic" "a")',
                 'dbReplaceProp(b0Held "unsaved" "string" "private")']
    else:
        setup = ['procedure(b0FailedInspector(@rest b0Args) "{\\"ok\\":false}")',
                 "putd('aiInspectSchematic getd('b0FailedInspector))"]
    result, _ = probe(tmp_path / 'reader', design, target=target, before=setup)
    assert not result['ok'] and not result['available']
    assert result['code'] == 'background_unavailable' and result['reason'] == reason
    assert fingerprints(design) == before


def test_explicit_target_and_supported_method_required():
    from sico.service.background_capability import build_background_probe
    for method, arguments in [('eval_skill', TARGET), ('inspect_schematic', {}),
                               ('inspect_schematic', dict(TARGET, window=1)),
                               ('inspect_schematic',
                                dict(TARGET, oa_view_type='maskLayout'))]:
        with pytest.raises(ValueError):
            build_background_probe(method, arguments)


@pytest.mark.parametrize('raw', ['{}', 'null', '{', '{"ok":true,"ok":false}',
                               '{"ok":true,"available":true}', '{"protocol":"old"}',
                               b'\xff', '{"value":NaN}', 'x' * (256 * 1024 + 1)])
def test_incomplete_or_invalid_probe_is_not_a_capability(raw):
    from sico.service.background_capability import assess_background_probe
    result = assess_background_probe(raw, target=TARGET, expected_version='IC231')
    assert result['code'] == 'background_unavailable' and not result['available']


def test_dbaccess_can_read_existing_cdf(design, tmp_path):
    before = fingerprints(design)
    result, _ = probe(tmp_path / 'dbaccess', design, binary=DB)
    assert result['available']
    assert result['evidence']['dependencies'][0]['cdf_parameter_count'] == 1
    assert fingerprints(design) == before


@pytest.mark.parametrize('missing, reason', [('B0Devices', 'master_unavailable'),
                                           ('B0Tech', 'technology_unavailable')])
def test_missing_library_dependency_fails_closed(design, tmp_path, missing, reason):
    before = fingerprints(design)
    isolated = tmp_path / 'missing'
    isolated.mkdir()
    (isolated / 'cds.lib').write_text('\n'.join(
        line for line in (design / 'cds.lib').read_text().splitlines()
        if missing not in line) + '\n')
    result, _ = probe(tmp_path / 'reader', isolated)
    assert result['reason'] == reason and not result['available']
    assert fingerprints(design) == before


@pytest.fixture
def valid_evidence():
    # Protocol checks remain runnable without a Cadence installation or license.
    return dict(protocol='cad_ai_background_probe.v1', ok=True,
                target={k: TARGET[k] for k in ('lib', 'cell', 'view')},
                virtuoso_version='IC231', open_mode='r', modified=False, closed=True,
                window_required=False, callbacks_executed=False, technology_library='B0Tech',
                instance_count=1, dependencies=[dict(instance='I0', open_mode='r', modified=False,
                    master=dict(lib='B0Devices', cell='leaf', view='symbol'),
                    technology_library='B0Tech', cdf_present=True, cdf_parameter_count=1)],
                inspection=dict(ok=True, kind='schematic', modified=False, truncated=False,
                    cellview={k: TARGET[k] for k in ('lib', 'cell', 'view')},
                    instances=[dict(name='I0', lib='B0Devices', cell='leaf', view='symbol')],
                    nets=[], terminals=[]))


def test_valid_protocol_evidence_is_scoped_to_saved_oa(valid_evidence):
    from sico.service.background_capability import assess_background_probe
    result = assess_background_probe(json.dumps(valid_evidence), target=TARGET, expected_version='IC231')
    assert result['available'] and result['snapshot_scope'] == 'saved_oa'
    assert not result['foreground_memory_visible'] and not result['automatic_resume_allowed']


@pytest.mark.parametrize('field, value', [
    ('target', {'lib': 'other', 'cell': 'top', 'view': 'schematic'}),
    ('virtuoso_version', 'old'), ('closed', False), ('modified', True),
    ('callbacks_executed', True), ('window_required', True), ('dependencies', []),
    ('dependencies', [{'master': {}}]), ('inspection', {'ok': False}),
])
def test_changed_evidence_never_qualifies(valid_evidence, field, value):
    from sico.service.background_capability import assess_background_probe
    raw = dict(valid_evidence)
    version = raw['virtuoso_version']
    raw[field] = value
    result = assess_background_probe(json.dumps(raw), target=TARGET, expected_version=version)
    assert not result['available'] and result['code'] == 'background_unavailable'


@pytest.mark.parametrize('failure, reason', [('cdf', 'cdf_unavailable'),
                                           ('close_nil', 'close_failed'),
                                           ('close_error', 'close_failed'),
                                           ('api', 'api_unavailable')])
def test_native_api_failures_and_cleanup(design, tmp_path, failure, reason):
    before = fingerprints(design)
    # Internal Cadence functions cannot be redefined. Redirect only the copied
    # probe's dependency call; production source/context stays unmodified.
    injected = (CAD / 'ai/skill/AI_background_probe.il').read_text()
    if failure == 'cdf':
        injected = injected.replace('cdfGetInstCDF', 'b0InjectedCdf')
        setup = ['procedure(b0InjectedCdf(@rest b0Args) nil)']
        cleanup = ['when(dbFindOpenCellViewByName("B0Work" "top" "schematic") '
                   'error("Probe leaked cellview"))']
    elif failure == 'api':
        injected = injected.replace('cdfGetInstCDF', 'b0MissingCdfApi')
        setup = []
        cleanup = []
    else:
        injected = injected.replace('dbClose', 'b0InjectedClose')
        setup = ['procedure(b0InjectedClose(b0CloseCv) ' +
                 ('nil' if failure == 'close_nil' else 'error("injected")') + ')']
        cleanup = ['b0Remaining=dbFindOpenCellViewByName("B0Work" "top" "schematic")',
                   'when(b0Remaining dbClose(b0Remaining))']
    source = tmp_path / 'injected.il'
    source.write_text(injected)
    bootstrap = loads()[:-1] + setup + [f'load({json.dumps(str(source))})']
    result, _ = probe(tmp_path / 'reader', design, bootstrap=bootstrap, after=cleanup)
    assert not result['available'] and result['reason'] == reason, result
    assert fingerprints(design) == before


def test_ordinary_symbol_without_base_cdf_is_supported(design, tmp_path):
    result, raw = probe(tmp_path / 'reader', design, target=dict(TARGET, cell='plain'))
    assert result['available'], result
    assert raw['dependencies'][0]['cdf_parameter_count'] == 0


def test_unqualified_hierarchical_symbol_fails_closed(design, tmp_path):
    before = fingerprints(design)
    result, _ = probe(tmp_path / 'reader', design, target=dict(TARGET, cell='nested'))
    assert not result['available'] and result['reason'] == 'unsupported_dependency'
    assert fingerprints(design) == before


def test_same_process_writer_handle_is_preserved(design, tmp_path):
    result, _ = probe(tmp_path / 'reader', design, before=[
        'b0Held=dbOpenCellViewByType("B0Work" "top" "schematic" "schematic" "a")',
        'dbCreateNet(b0Held "UNSAVED")',
    ], after=[
        'unless(and(dbIsCellViewModified(b0Held) dbFindNetByName(b0Held "UNSAVED")) '
        'error("Probe changed existing writer"))', 'dbPurge(b0Held)',
    ])
    assert result['reason'] == 'writable_handle'


@pytest.mark.skipif(not os.environ.get('DISPLAY'), reason='Graphical foreground needs DISPLAY')
def test_foreground_unsaved_edit_is_invisible_to_independent_reader(design, tmp_path):
    before = fingerprints(design)
    foreground = tmp_path / 'foreground'
    foreground.mkdir(mode=0o700)
    source = foreground / 'foreground.il'
    source.write_text('\n'.join([
        'b0Window=geOpen(?lib "B0Work" ?cell "top" ?view "schematic" ?mode "a")',
        'unless(b0Window error("Foreground window missing"))',
        'b0Held=geGetWindowCellView(b0Window)',
        'dbCreateNet(b0Held "UNSAVED")',
        'unless(dbIsCellViewModified(b0Held) error("Foreground edit missing"))',
        'b0Port=outfile("ready") fprintf(b0Port "%L" b0Window) close(b0Port)',
        'procedure(b0ForegroundTick()',
        '  if(isFile("stop") then',
        '    unless(and(dbIsCellViewModified(b0Held) dbFindNetByName(b0Held "UNSAVED"))',
        '      error("Foreground edit lost"))',
        '    dbPurge(b0Held)',
        '    b0Port=outfile("done") fprintf(b0Port "ok") close(b0Port) exit(0)',
        '  else hiRegTimer("b0ForegroundTick()" 2)))',
        'hiRegTimer("b0ForegroundTick()" 2)',
    ]) + '\n')
    with (foreground / 'stdout.log').open('w') as log:
        process = subprocess.Popen([str(IC), '-nocdsinit', '-cdslib', str(design / 'cds.lib'),
                                    '-restore', str(source), '-log', str(foreground / 'CDS.log')],
                                   cwd=foreground, env=environment(foreground, graphical=True),
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 25
            while not (foreground / 'ready').exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
            assert (foreground / 'ready').exists(), (foreground / 'stdout.log').read_text()
            result, raw = probe(tmp_path / 'reader', design)
            assert result['available'], result
            assert [net['name'] for net in raw['inspection']['nets']] == ['SAVED']
            assert process.poll() is None and fingerprints(design) == before
            (foreground / 'stop').touch()
            assert process.wait(timeout=10) == 0
            assert (foreground / 'done').read_text() == 'ok'
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    assert fingerprints(design) == before
    output = (foreground / 'CDS.log').read_text()
    assert '*Error*' not in output and not (foreground / 'callback-executed').exists(), output


def test_compiled_context_relocated_without_source_access(design, tmp_path):
    if not shutil.which('bwrap'):
        pytest.skip('Source isolation requires bwrap')
    before = fingerprints(design)
    context = tmp_path / 'build' / 'cadB0.cxt'
    smoke = tmp_path / 'smoke.il'
    smoke.write_text('unless(aiBackgroundProbeRevision()=="20260915.ai.background.probe.v1" '
                     'error("Missing B0 context"))\n'
                     'unless(stringp(aiBackgroundProbe(nil nil nil "schematic" 50 nil)) '
                     'error("Probe cannot reject missing target"))\n')
    command = [sys.executable, str(CAD.parent / 'deploy/build_skill_context.py'), str(context)]
    for name in SOURCES:
        command += ['--source', str(CAD / 'ai/skill' / name)]
    command += ['--probe', str(smoke), '--expected-version', (design / 'passed').read_text(),
                '--virtuoso', str(IC), '--timeout', '60']
    build = subprocess.run(command, cwd=tmp_path, env=environment(tmp_path),
                           capture_output=True, text=True, timeout=150)
    assert build.returncode == 0, build.stdout + build.stderr
    receipt = json.loads(context.with_suffix('.build.json').read_text())
    assert receipt['read_protect'] and receipt['write_protect']
    assert not receipt['keep_source_info']
    relocated = tmp_path / 'relocated'
    relocated.mkdir(mode=0o700)
    copied = relocated / context.name
    shutil.copyfile(context, copied)
    assert hashlib.sha256(copied.read_bytes()).hexdigest() == receipt['sha256']
    prefix = ['bwrap', '--ro-bind', '/', '/', '--bind', '/tmp', '/tmp',
              '--dev-bind', '/dev', '/dev', '--proc', '/proc', '--tmpfs', str(CAD),
              '--tmpfs', str(context.parent)]
    result, _ = probe(relocated / 'reader', design, prefix=prefix, bootstrap=[
        f'when(isFile({json.dumps(str(CAD / "ai/skill/AI_inspect.il"))}) error("Source visible"))',
        f'unless(loadContext({json.dumps(str(copied))} t) error("Context load failed"))',
    ])
    assert result['available'], result
    assert fingerprints(design) == before
