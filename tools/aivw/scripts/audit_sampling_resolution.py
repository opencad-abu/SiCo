#!/usr/bin/env python3
"""Authenticate public references, then sweep event-window sampling steps."""

import argparse
import json
from pathlib import Path

from aivworkbench.manifest import resolve_indexed_artifact, publish_split_manifests, verify_split_manifests
from aivworkbench.qualification.sampling_resolution import assess_resolution
from aivworkbench.waveform_metrics import read_waveform
from aivworkbench.workspace import allocate_split_run, resolve_launch_paths, sha256_file, stable_digest, write_json_once


def audit(recipe_path):
    recipe = json.loads(recipe_path.read_text())
    inputs, targets = [], {}
    for target, rid in recipe['targets'].items():
        control = Path('.aivw/runs')/rid/'manifest.json'
        value = json.loads(control.read_text())
        root = Path(value['details']['payload_root'])
        pair = verify_split_manifests(control, root/'payload-manifest.json')
        def path(name):
            return resolve_indexed_artifact(control, name).path
        def read(name):
            return json.loads(path(name).read_text())
        plan_path = path('checks/gates/public_data/experiment.json')
        plan = json.loads(plan_path.read_text())
        evidence = read('checks/gates/public_data/characterization-evidence.json')
        policy = read('checks/gates/correlation/acceptance-policy.json')
        if (evidence['dataset'] != 'public_calibration' or evidence['source_recheck'] != 'PASS' or
                evidence['experiment_digest'] != stable_digest(plan) or evidence['experiment_sha256'] != sha256_file(plan_path) or
                evidence['target'] != plan['target'] or
                [c['id'] for c in evidence['cases']] != [c['id'] for c in plan['cases']]):
            raise ValueError('public experiment binding differs')
        reports = []
        signals = {p: 'V' for p in plan['target']['term_order']}
        signals['VPROBE:p'] = 'A'
        for case, source in zip(plan['cases'], evidence['cases']):
            if source['case_digest'] != stable_digest(case):
                raise ValueError('source case binding differs')
            events = [e for e in case['events'] if e['signal'] in recipe['event_signals']]
            if not events:
                continue
            csvs = [a for a in source['artifacts'] if a['path'].endswith('/waveforms.csv')]
            if len(csvs) != 1 or sha256_file(path(csvs[0]['path'])) != csvs[0]['sha256']:
                raise ValueError('source waveform binding differs')
            wave = read_waveform(path(csvs[0]['path']), signals, plan['analysis']['stop_s'])
            for event in events:
                start = event['start_s']-recipe['pre_event_s']
                stop = event['end_s']+recipe['post_event_s']
                result = assess_resolution(wave, output=plan['roles']['output'], current='VPROBE:p',
                    start=start, stop=stop, steps=recipe['steps_s'], policy=policy, definition=plan['measurement'])
                reports.append({'case': case['id'], 'event': event['id'], 'signal': event['signal'], **result})
                print(json.dumps({'target': target, 'case': case['id'], 'event': event['id'],
                    'steps': {str(r['step_s']): r['diagnostic_status'] for r in result['steps']}}), flush=True)
        verify_split_manifests(control, root/'payload-manifest.json')
        inputs.append(pair)
        targets[target] = {'target': plan['target'], 'source_generation': evidence['source_generation'],
                           'policy_digest': stable_digest(policy), 'events': reports}
    module = Path(__file__).resolve().parents[1]/'python/aivworkbench/qualification/sampling_resolution.py'
    request = {'kind': recipe['kind'], 'recipe_sha256': sha256_file(recipe_path), 'sources': inputs,
               'implementation': {str(p): sha256_file(p) for p in [Path(__file__).resolve(), module]}}
    digest = stable_digest(request)
    run = allocate_split_run(resolve_launch_paths(), Path('/path/to/xh/amsVerify/data/ams').resolve(),
        library='aivw', cell='dynamic_sampling', view='public_diagnostic', kind='sampling-audit', request_digest=digest)
    summary = {'status': 'PUBLIC_SAMPLING_DIAGNOSTICS_RECORDED', 'qualification': 'NOT_ESTABLISHED',
               'holdout_evaluated': False, 'targets': targets}
    write_json_once(run.control_root/'request.json', request)
    write_json_once(run.payload_root/'summary.json', summary)
    publish_split_manifests(run, request_digest=digest, status=summary['status'],
        control_details={'payload_root': str(run.payload_root)}, payload_details={'scope': 'public_reference_reconstruction_only'},
        control_artifacts=[run.control_root/'request.json'], payload_artifacts=[run.payload_root/'summary.json'])
    verify_split_manifests(run.control_manifest, run.payload_manifest)
    print(json.dumps({'run_id': run.run_id, 'summary': str(run.payload_root/'summary.json')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recipe', type=Path)
    audit(parser.parse_args().recipe)
