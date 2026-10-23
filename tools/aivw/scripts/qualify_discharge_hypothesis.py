#!/usr/bin/env python3
"""Public-data nonlinear discharge hypothesis with independent SV execution.

No private holdout or new source simulation. A public fast tail supplies the
rate law; all public trajectories, including other rates, are checked afterward.
"""

from copy import deepcopy
import argparse
import json
from pathlib import Path

from aivworkbench.executors.trajectory_correlation import correlation_report
from aivworkbench.executors.trajectory_xcelium import execute_candidate
from aivworkbench.manifest import resolve_indexed_artifact, publish_split_manifests, verify_split_manifests
from aivworkbench.plugins.discharge_state import fit_discharge, apply_discharge
from aivworkbench.plugins.sampled_state import inputs_at
from aivworkbench.plugins.sampled_state_sv import render_model, render_testbench
from aivworkbench.profiles import load_profile
from aivworkbench.toolchain import capture_module_environment, probe_tools
from aivworkbench.waveform_metrics import Waveform, read_waveform
from aivworkbench.workspace import allocate_split_run, resolve_launch_paths, sha256_file, stable_digest, write_json_once


def qualify(recipe_path):
    recipe = json.loads(recipe_path.read_text())
    control = Path('.aivw/runs')/recipe['parent_run']/'manifest.json'
    old = json.loads(control.read_text())
    root = Path(old['details']['payload_root'])
    pair = verify_split_manifests(control, root/'payload-manifest.json')
    def path(relative):
        return resolve_indexed_artifact(control, relative).path
    def read(relative):
        return json.loads(path(relative).read_text())
    model = read('checks/gates/model/model.json')
    plan = read('checks/gates/public_data/experiment.json')
    evidence = read('checks/gates/public_data/characterization-evidence.json')
    parent_report = read('checks/gates/correlation/correlation.json')
    policy = read('checks/gates/correlation/acceptance-policy.json')
    rnm = read('checks/gates/rnm/trajectory-evidence.json')
    parent_source = path('checks/gates/model/candidate.sv')
    identity = dict(model)
    digest = identity.pop('model_digest')
    if (evidence['dataset'] != 'public_calibration' or model['dataset'] != 'public_calibration' or
            stable_digest(identity) != digest or render_model(model) != parent_source.read_text() or
            any(x['experiment_digest'] != stable_digest(plan) for x in (model, evidence, rnm, parent_report)) or
            any(x['source_generation'] != evidence['source_generation'] for x in (model, rnm, parent_report)) or
            any(x['candidate_sha256'] != sha256_file(parent_source) for x in (rnm, parent_report)) or
            rnm['status'] != 'EXECUTED' or parent_report['policy_digest'] != stable_digest(policy)):
        raise ValueError('public parent/source bindings differ')
    ids = [c['id'] for c in plan['cases']]
    if any([c['id'] for c in x['cases']] != ids for x in (evidence, rnm, parent_report)):
        raise ValueError('public case coverage differs')
    signals = {p: 'V' for p in plan['target']['term_order']}
    signals['VPROBE:p'] = 'A'
    output = plan['roles']['output']
    golden, baseline = {}, {}
    for case, source, simulated in zip(plan['cases'], evidence['cases'], rnm['cases']):
        if any(x['case_digest'] != stable_digest(case) for x in (source, simulated)):
            raise ValueError('case digest differs')
        csvs = [a for a in source['artifacts'] if a['path'].endswith('/waveforms.csv')]
        if len(csvs) != 1 or sha256_file(path(csvs[0]['path'])) != csvs[0]['sha256'] or sha256_file(path(simulated['csv'])) != simulated['sha256']:
            raise ValueError('waveform binding differs')
        golden[case['id']] = read_waveform(path(csvs[0]['path']), signals, plan['analysis']['stop_s'])
        baseline[case['id']] = read_waveform(path(simulated['csv']), {output: 'V', 'VPROBE:p': 'A'}, plan['analysis']['stop_s'])
    training = next(c for c in plan['cases'] if c['id'] == recipe['training_case'])
    event = next(e for e in training['events'] if e['id'] == recipe['training_event'])
    if event['signal'] != recipe['control'] or recipe['control'] not in model['roles']['controls']:
        raise ValueError('discharge training control differs')
    index = next(i for i, a in enumerate(model['axes']) if a['name'] == recipe['control'])
    values = inputs_at(plan, training, event['end_s'])
    if values[index] != 0.:
        raise ValueError('discharge training does not end at zero control')
    equilibrium = golden[training['id']].sample(output, plan['analysis']['stop_s'])
    spec = fit_discharge(golden[training['id']], output=output, start=event['end_s'],
        stop=event['end_s']+recipe['tail_duration_s'], equilibrium=equilibrium, step=model['step_s'], knots=recipe['knots'])
    model = deepcopy(model)
    model['discharge'] = {**spec, 'control_axis': index, 'supply_v': values[0], 'load_conductance_s': values[-1]}
    model['scope'] = recipe['scope']
    model['revision_parent_sha256'] = sha256_file(parent_source)
    model.pop('model_digest')
    model['model_digest'] = stable_digest(model)
    predicted = {}
    for case in plan['cases']:
        base = baseline[case['id']]
        previous = inputs_at(plan, case, 0.)
        outputs = []
        for i, t in enumerate(base.times):
            values = inputs_at(plan, case, t)
            value = apply_discharge(model, base.values[output][i], outputs[-1] if outputs else base.values[output][i], values, previous, i == 0)
            outputs.append(value)
            previous = values
        predicted[case['id']] = Waveform(base.times, base.signals, {output: tuple(outputs), 'VPROBE:p': base.values['VPROBE:p']})
    profile = load_profile('amsverify')
    env = capture_module_environment(profile.modules)
    qualified = probe_tools([t for t in profile.tools if t.name == 'xrun'], env)
    if len(qualified) != 1 or qualified[0].status != 'PASS':
        raise ValueError('qualified Xcelium unavailable')
    package = Path(__file__).resolve().parents[1]
    request = {'kind': 'public-discharge-hypothesis', 'recipe_sha256': sha256_file(recipe_path),
        'parent_pair': pair, 'training_case': recipe['training_case'], 'dataset': 'public_calibration',
        'tools': [q.to_dict() for q in qualified], 'implementation': {str(p): sha256_file(p) for p in [
            Path(__file__).resolve(), *[package/'python/aivworkbench'/n for n in (
                'plugins/discharge_state.py', 'plugins/sampled_state_sv.py', 'executors/trajectory_xcelium.py',
                'executors/trajectory_correlation.py', 'waveform_metrics.py')]]}, 'holdout_evaluated': False}
    digest = stable_digest(request)
    run = allocate_split_run(resolve_launch_paths(), Path('/path/to/xh/amsVerify/data/ams').resolve(),
        library=plan['target']['library'], cell=plan['target']['cell'], view='discharge_hypothesis',
        kind='discharge-hypothesis', request_digest=digest)
    write_json_once(run.control_root/'request.json', request)
    summary = {'status': 'FAIL_EXECUTION', 'qualification': 'NOT_ESTABLISHED', 'holdout_evaluated': False}
    try:
        write_json_once(run.payload_root/'model.json', model)
        write_json_once(run.payload_root/'experiment.json', plan)
        write_json_once(run.payload_root/'acceptance-policy.json', policy)
        source, records = execute_candidate(run.payload_root/'xcelium', render_model(model), model, plan,
            env, {'xrun': qualified[0].path}, 900., render_testbench)
        actual = {r['id']: read_waveform(r['csv'], {output: 'V', 'VPROBE:p': 'A'}, plan['analysis']['stop_s']) for r in records}
        parity = []
        for case in plan['cases']:
            a, b = actual[case['id']], predicted[case['id']]
            if a.times != b.times:
                raise ValueError('implementation axes differ')
            errors = {s: max(abs(x-y) for x, y in zip(a.values[s], b.values[s])) for s in (output, 'VPROBE:p')}
            parity.append({'id': case['id'], 'max_absolute_error': errors,
                           'status': 'PASS' if errors[output] < 1e-9 and errors['VPROBE:p'] < 1e-12 else 'FAIL'})
        report = correlation_report(plan, evidence, golden, actual, policy, sha256_file(source))
        write_json_once(run.payload_root/'correlation.json', report)
        write_json_once(run.payload_root/'implementation-parity.json', {'checks': parity})
        rows = [{'id': c['id'], 'parent_diagnostic': p['diagnostic_status'], 'diagnostic_status': c['diagnostic_status'],
                 'voltage': c['waveforms']['voltage'], 'current': c['waveforms']['current']} for c, p in zip(report['cases'], parent_report['cases'])]
        summary = {'status': report['status'], 'diagnostic_status': report['diagnostic_status'],
            'qualification': 'NOT_ESTABLISHED', 'candidate_sha256': sha256_file(source), 'cases': rows,
            'passed_cases': sum(c['diagnostic_status'] == 'PASS' for c in rows), 'case_count': len(rows),
            'implementation_parity': 'PASS' if all(c['status'] == 'PASS' for c in parity) else 'FAIL',
            'training_case': recipe['training_case'], 'holdout_evaluated': False, 'publication': 'NOT_PERFORMED'}
        if summary['implementation_parity'] != 'PASS':
            summary['status'] = 'FAIL_IMPLEMENTATION_PARITY'
        if any(sha256_file(Path(p)) != h for p, h in request['implementation'].items()):
            raise ValueError('implementation changed during experiment')
        verify_split_manifests(control, root/'payload-manifest.json')
    finally:
        write_json_once(run.payload_root/'summary.json', summary)
        publish_split_manifests(run, request_digest=digest, status=summary['status'],
            control_details={'payload_root': str(run.payload_root)}, payload_details={'scope': recipe['scope']},
            control_artifacts=[run.control_root/'request.json'], payload_artifacts=[p for p in run.payload_root.rglob('*') if p.is_file()])
        verify_split_manifests(run.control_manifest, run.payload_manifest)
        print(json.dumps({'run_id': run.run_id, **summary}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recipe', type=Path)
    qualify(parser.parse_args().recipe)
