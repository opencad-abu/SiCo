"""Re-audit a public frozen-repeat certificate and every child waveform."""

import json
from pathlib import Path

from ..manifest import resolve_indexed_artifact, verify_split_manifests
from ..workspace import sha256_file, stable_digest
from .frozen_repeats import compare_attempts, verify_freeze


def read_public_repeat_certificate(control):
    control = Path(control)
    value = json.loads(control.read_text())
    root = Path(value['details']['payload_root'])
    pair = verify_split_manifests(control, root/'payload-manifest.json')
    def read(relative):
        return json.loads(resolve_indexed_artifact(control, relative).path.read_text())
    summary = read('summary.json')
    request_records = [r for r in value['artifacts'] if r['path'] == 'request.json']
    request_path = control.parent/'request.json'
    if len(request_records) != 1 or request_path.is_symlink() or sha256_file(request_path) != request_records[0]['sha256']:
        raise ValueError('control request is not authenticated')
    request = json.loads(request_path.read_text())
    if (summary['status'] != 'PASS_PUBLIC_FROZEN_REPEAT' or pair['run_status'] != summary['status'] or
            summary['dataset'] != 'public_calibration' or summary['holdout_evaluated'] is not False or
            summary['source_simulation_repeated'] is not False or len(summary['children']) != 2):
        raise ValueError('not a complete public-only repeat certificate')
    journal = root/'journal'
    frozen = verify_freeze(journal, summary['freeze_digest'])
    if frozen['bindings'] != request:
        raise ValueError('freeze/request binding differs')
    actual_comparison = compare_attempts(journal, summary['freeze_digest'])
    if actual_comparison != summary['comparison'] or actual_comparison['status'] != 'PASS_REPEAT':
        raise ValueError('repeat comparison differs')
    prior = read('journal/inputs/prior-rnm.json')
    plan = read('journal/inputs/experiment.json')
    if (prior['dataset'] if 'dataset' in prior else 'public_calibration') != 'public_calibration':
        raise ValueError('not a public prior run')
    if prior['experiment_digest'] != stable_digest(plan):
        raise ValueError('prior experiment differs')
    expected = {c['id']: c['sha256'] for c in prior['cases']}
    old_control = Path(request['source_pair']['control_manifest'])
    old_value = json.loads(old_control.read_text())
    old_pair = verify_split_manifests(old_control, Path(old_value['details']['payload_root'])/'payload-manifest.json')
    if old_pair != request['source_pair']:
        raise ValueError('original source manifest differs')
    for c in prior['cases']:
        artifact = resolve_indexed_artifact(old_control, c['csv']).path
        if sha256_file(artifact) != c['sha256']:
            raise ValueError('original public waveform differs')
    for number, child in enumerate(summary['children'], 1):
        child_control = Path(child['control_manifest'])
        child_value = json.loads(child_control.read_text())
        child_pair = verify_split_manifests(child_control, Path(child_value['details']['payload_root'])/'payload-manifest.json')
        if (child_pair['run_id'] != child['run_id'] or child_pair['control_manifest_sha256'] != child['sha256'] or
                child_pair['run_status'] != 'PASS_EXECUTION' or child_value['details']['parent'] != pair['run_id'] or
                child_value['details']['repeat'] != number or child_value['details']['freeze_digest'] != summary['freeze_digest']):
            raise ValueError('child repeat binding differs')
        report = json.loads(resolve_indexed_artifact(child_control, 'summary.json').path.read_text())
        attempt = read('journal/attempt_%d/result.json' % number)
        if (report != child['summary'] or report['waveform_sha256'] != expected or
                attempt['measurements'] != expected or attempt['run_id'] != child['run_id'] or
                report['case_count'] != len(plan['cases']) or not report['identical_to_original_public_run']):
            raise ValueError('child measurements differ')
        for case in plan['cases']:
            csv = resolve_indexed_artifact(child_control, 'xcelium/'+case['id']+'/waveforms.csv').path
            if sha256_file(csv) != expected[case['id']]:
                raise ValueError('repeated waveform differs')
    return {'status': 'PASS_PUBLIC_REPEAT_EVIDENCE', 'run_id': pair['run_id'],
            'sha256': pair['control_manifest_sha256'], 'case_count': len(plan['cases']),
            'repeat_count': 2, 'candidate_sha256': request['candidate_sha256'],
            'qualification': 'NOT_ESTABLISHED', 'holdout_evaluated': False}
