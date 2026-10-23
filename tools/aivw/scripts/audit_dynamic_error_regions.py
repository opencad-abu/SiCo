#!/usr/bin/env python3
"""Bind public input-region diagnostics to an existing candidate correlation."""

import argparse
import json
from pathlib import Path

from aivworkbench.manifest import resolve_indexed_artifact, publish_split_manifests, verify_split_manifests
from aivworkbench.qualification.error_regions import diagnose_regions
from aivworkbench.waveform_metrics import read_waveform
from aivworkbench.workspace import allocate_split_run, resolve_launch_paths, sha256_file, stable_digest, write_json_once


def audit(control):
    value = json.loads(control.read_text())
    root = Path(value['details']['payload_root'])
    pair = verify_split_manifests(control, root/'payload-manifest.json')
    def path(name):
        return resolve_indexed_artifact(control, name).path
    def read(name):
        return json.loads(path(name).read_text())
    plan = read('checks/gates/public_data/experiment.json')
    source = read('checks/gates/public_data/characterization-evidence.json')
    rnm = read('checks/gates/rnm/trajectory-evidence.json')
    correlation = read('checks/gates/correlation/correlation.json')
    policy = read('checks/gates/correlation/acceptance-policy.json')
    candidate = path('checks/gates/model/candidate.sv')
    if (source['dataset'] != 'public_calibration' or correlation['dataset'] != 'public_calibration' or
            any(v['experiment_digest'] != stable_digest(plan) for v in (source, rnm, correlation)) or
            any(v['source_generation'] != source['source_generation'] for v in (rnm, correlation)) or
            correlation['policy_digest'] != stable_digest(policy) or
            any(v['candidate_sha256'] != sha256_file(candidate) for v in (rnm, correlation)) or
            rnm['status'] != 'EXECUTED'):
        raise ValueError('diagnostic public input identities differ')
    ids = [c['id'] for c in plan['cases']]
    if any([c['id'] for c in v['cases']] != ids for v in (source, rnm, correlation)):
        raise ValueError('diagnostic case coverage differs')
    cases = []
    for case, s, r in zip(plan['cases'], source['cases'], rnm['cases']):
        if s['case_digest'] != stable_digest(case) or r['case_digest'] != stable_digest(case):
            raise ValueError('diagnostic case binding differs')
        csvs = [x for x in s['artifacts'] if x['path'].endswith('/waveforms.csv')]
        if len(csvs) != 1:
            raise ValueError('one golden waveform required')
        gpath, rpath = path(csvs[0]['path']), path(r['csv'])
        if sha256_file(gpath) != csvs[0]['sha256'] or sha256_file(rpath) != r['sha256']:
            raise ValueError('waveform binding differs')
        signals = {p: 'V' for p in plan['target']['term_order']}
        signals['VPROBE:p'] = 'A'
        golden = read_waveform(gpath, signals, plan['analysis']['stop_s'])
        actual = read_waveform(rpath, {plan['roles']['output']: 'V', 'VPROBE:p': 'A'}, plan['analysis']['stop_s'])
        report = diagnose_regions(actual, golden, output=plan['roles']['output'], current='VPROBE:p',
                                   events=case['events'], policy=policy, stimuli=golden)
        cases.append({'id': case['id'], 'case_digest': stable_digest(case), **report})
    verify_split_manifests(control, root/'payload-manifest.json')
    request = {'kind': 'public-dynamic-error-regions', 'source_pair': pair,
               'candidate_sha256': sha256_file(candidate), 'correlation_digest': stable_digest(correlation),
               'implementation': {str(p): sha256_file(p) for p in [Path(__file__).resolve(),
                   Path(__file__).resolve().parents[1]/'python/aivworkbench/qualification/error_regions.py']}}
    digest = stable_digest(request)
    run = allocate_split_run(resolve_launch_paths(), Path('/path/to/xh/amsVerify/data/ams').resolve(),
        library=plan['target']['library'], cell=plan['target']['cell'], view='public_error_regions',
        kind='error-region-audit', request_digest=digest)
    summary = {'status': 'PUBLIC_DIAGNOSTICS_RECORDED', 'qualification': 'NOT_ESTABLISHED',
               'holdout_evaluated': False, 'cases': cases, 'original_correlation_status': correlation['status']}
    write_json_once(run.control_root/'request.json', request)
    write_json_once(run.payload_root/'summary.json', summary)
    publish_split_manifests(run, request_digest=digest, status=summary['status'],
        control_details={'payload_root': str(run.payload_root)}, payload_details={'scope': 'public_diagnostics_only'},
        control_artifacts=[run.control_root/'request.json'], payload_artifacts=[run.payload_root/'summary.json'])
    verify_split_manifests(run.control_manifest, run.payload_manifest)
    print(json.dumps({'run_id': run.run_id, 'summary': str(run.payload_root/'summary.json')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    audit(parser.parse_args().manifest.resolve())
