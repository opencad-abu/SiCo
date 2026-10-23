#!/usr/bin/env python3
"""Execute two frozen PUBLIC Xcelium repeats; never a final holdout verdict."""

import argparse
import json
from pathlib import Path

from aivworkbench.executors.trajectory_xcelium import execute_candidate
from aivworkbench.manifest import publish_split_manifests, resolve_indexed_artifact, verify_split_manifests
from aivworkbench.plugins.sampled_state_sv import render_model, render_testbench
from aivworkbench.profiles import load_profile
from aivworkbench.qualification.frozen_repeats import freeze_inputs, verify_freeze, begin_attempt, finish_attempt, compare_attempts
from aivworkbench.toolchain import capture_module_environment, probe_tools
from aivworkbench.workspace import allocate_split_run, resolve_launch_paths, sha256_file, stable_digest, write_json_once


def audit(control):
    control = control.resolve()
    value = json.loads(control.read_text())
    old_root = Path(value['details']['payload_root'])
    pair = verify_split_manifests(control, old_root/'payload-manifest.json')
    def artifact(name):
        return resolve_indexed_artifact(control, name).path
    files = {name: artifact(relative) for name, relative in {
        'model.json': 'checks/gates/model/model.json', 'candidate.sv': 'checks/gates/model/candidate.sv',
        'experiment.json': 'checks/gates/public_data/experiment.json',
        'policy.json': 'checks/gates/correlation/acceptance-policy.json',
        'source.json': 'checks/gates/public_data/characterization-evidence.json',
        'prior-rnm.json': 'checks/gates/rnm/trajectory-evidence.json'}.items()}
    model, plan, source, prior = [json.loads(files[n].read_text()) for n in ('model.json', 'experiment.json', 'source.json', 'prior-rnm.json')]
    identity = dict(model)
    digest = identity.pop('model_digest')
    if (source['dataset'] != 'public_calibration' or model['dataset'] != 'public_calibration' or
            render_model(model) != files['candidate.sv'].read_text() or stable_digest(identity) != digest or
            any(x['experiment_digest'] != stable_digest(plan) for x in (source, model, prior)) or
            any(x['source_generation'] != source['source_generation'] for x in (model, prior)) or
            prior['candidate_sha256'] != sha256_file(files['candidate.sv']) or prior['status'] != 'EXECUTED'):
        raise ValueError('public source/candidate binding differs')
    if [c['id'] for c in prior['cases']] != [c['id'] for c in plan['cases']]:
        raise ValueError('original RNM case coverage differs')
    for c, p in zip(plan['cases'], prior['cases']):
        if p['case_digest'] != stable_digest(c) or sha256_file(artifact(p['csv'])) != p['sha256']:
            raise ValueError('original RNM artifact binding differs')
    expected = {c['id']: c['sha256'] for c in prior['cases']}
    profile = load_profile('amsverify')
    env = capture_module_environment(profile.modules)
    qualified = probe_tools([t for t in profile.tools if t.name == 'xrun'], env)
    if len(qualified) != 1 or qualified[0].status != 'PASS':
        raise ValueError('qualified Xcelium unavailable')
    package = Path(__file__).resolve().parents[1]/'python/aivworkbench'
    implementations = {str(p): sha256_file(p) for p in [Path(__file__).resolve(), *[
        package/n for n in ('qualification/frozen_repeats.py', 'plugins/sampled_state_sv.py',
                          'plugins/state_memory.py', 'executors/trajectory_xcelium.py', 'process.py')]]}
    request = {'kind': 'public-frozen-candidate-repeats', 'source_pair': pair,
               'candidate_sha256': sha256_file(files['candidate.sv']),
               'tool': qualified[0].to_dict(), 'implementation': implementations,
               'dataset': 'public_calibration', 'holdout_evaluated': False}
    request_digest = stable_digest(request)
    launch = resolve_launch_paths()
    db = Path('/path/to/xh/amsVerify/data/ams').resolve()
    run = allocate_split_run(launch, db, library=plan['target']['library'], cell=plan['target']['cell'],
                            view='public_frozen_repeat', kind='public-frozen-repeats', request_digest=request_digest)
    write_json_once(run.control_root/'request.json', request)
    journal = run.payload_root/'journal'
    frozen = freeze_inputs(journal, files, request)
    summary = {'status': 'BLOCKED_REPEAT', 'qualification': 'NOT_ESTABLISHED', 'dataset': 'public_calibration',
               'holdout_evaluated': False, 'source_simulation_repeated': False, 'publication': 'NOT_PERFORMED'}
    children = []
    try:
        for number in (1, 2):
            verify_freeze(journal, frozen['freeze_digest'])
            child = allocate_split_run(launch, db, library=plan['target']['library'], cell=plan['target']['cell'],
                view='public_frozen_repeat', kind='public-candidate-repeat', request_digest=request_digest)
            child_request = {'parent': run.run_id, 'repeat': number, 'freeze_digest': frozen['freeze_digest']}
            write_json_once(child.control_root/'request.json', child_request)
            begin_attempt(journal, frozen['freeze_digest'], number, child.run_id)
            status = 'FAIL_EXECUTION'
            try:
                _, records = execute_candidate(child.payload_root/'xcelium', (journal/'inputs/candidate.sv').read_text(),
                    json.loads((journal/'inputs/model.json').read_text()), json.loads((journal/'inputs/experiment.json').read_text()),
                    env, {'xrun': qualified[0].path}, 900., render_testbench)
                measurements = {c['id']: c['sha256'] for c in records}
                if measurements.keys() != expected.keys():
                    raise ValueError('repeat case coverage differs')
                if any(sha256_file(Path(p)) != h for p, h in implementations.items()):
                    raise ValueError('execution implementation changed')
                finish_attempt(journal, frozen['freeze_digest'], number, status='EXECUTED', measurements=measurements)
                status = 'PASS_EXECUTION'
                result = {'status': status, 'case_count': len(records), 'waveform_sha256': measurements,
                          'identical_to_original_public_run': measurements == expected}
            except Exception as exc:
                # No automatic retry; original attempt directory and logs remain.
                result = {'status': status, 'error_type': type(exc).__name__, 'reason': str(exc)}
                raise
            finally:
                write_json_once(child.payload_root/'summary.json', result)
                publish_split_manifests(child, request_digest=request_digest, status=status,
                    control_details={'payload_root': str(child.payload_root), **child_request}, payload_details={'dataset': 'public_calibration'},
                    control_artifacts=[child.control_root/'request.json'], payload_artifacts=[p for p in child.payload_root.rglob('*') if p.is_file()])
                checked = verify_split_manifests(child.control_manifest, child.payload_manifest)
                children.append({'run_id': child.run_id, 'control_manifest': str(child.control_manifest),
                                 'sha256': checked['control_manifest_sha256'], 'summary': result})
            print(json.dumps({'repeat': number, 'run_id': child.run_id, 'case_count': len(records)}), flush=True)
        compared = compare_attempts(journal, frozen['freeze_digest'])
        verify_split_manifests(control, old_root/'payload-manifest.json')
        same_original = all(c['summary']['identical_to_original_public_run'] for c in children)
        summary.update(status='PASS_PUBLIC_FROZEN_REPEAT' if compared['status'] == 'PASS_REPEAT' and same_original else 'FAIL_REPEAT',
                       comparison=compared, identical_to_original_public_run=same_original)
    finally:
        summary.update(children=children, freeze_digest=frozen['freeze_digest'])
        write_json_once(run.payload_root/'summary.json', summary)
        publish_split_manifests(run, request_digest=request_digest, status=summary['status'],
            control_details={'payload_root': str(run.payload_root)}, payload_details={'scope': 'public_reproducibility_only'},
            control_artifacts=[run.control_root/'request.json'], payload_artifacts=[p for p in run.payload_root.rglob('*') if p.is_file()])
        verify_split_manifests(run.control_manifest, run.payload_manifest)
        print(json.dumps({'run_id': run.run_id, 'status': summary['status'], 'qualification': summary['qualification']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    audit(parser.parse_args().manifest)
