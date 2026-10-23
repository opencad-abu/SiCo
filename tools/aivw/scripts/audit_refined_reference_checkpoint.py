#!/usr/bin/env python3
"""Independent evidence consistency audit for the complete public matrix."""
import argparse
import json
from pathlib import Path
from aivworkbench.manifest import verify_split_manifests,resolve_indexed_artifact,publish_split_manifests
from aivworkbench.workspace import allocate_split_run,resolve_launch_paths,sha256_file,stable_digest,write_json_once


def audit(config_path):
    config=json.loads(config_path.read_text());rows=[]
    for target,rid in config['matrix_runs'].items():
        control=Path('.aivw/runs')/rid/'manifest.json';root=Path(json.loads(control.read_text())['details']['payload_root'])
        pair=verify_split_manifests(control,root/'payload-manifest.json')
        def path(name):return resolve_indexed_artifact(control,name).path
        def read(name):return json.loads(path(name).read_text())
        request=json.loads((control.parent/'request.json').read_text());summary=read('summary.json');plan=read('experiment.json');policy=read('acceptance-policy.json')
        if stable_digest(request)!=pair['request_digest']:raise ValueError('request binding differs')
        if summary['qualification']!='NOT_ESTABLISHED' or summary['holdout_evaluated'] or summary['status']!='BLOCKED_CONTRACT':raise ValueError('qualification scope differs')
        if summary['live_source_recheck']!='PASS' or not summary['physical_stimuli_unchanged'] or summary['candidate_repeat']!='PASS':raise ValueError('source/candidate scope differs')
        for parent in request['sources']:
            if verify_split_manifests(Path(parent['control_manifest']),Path(parent['payload_manifest']))!=parent:raise ValueError('source manifest changed')
        candidate=path('xcelium/candidate.sv');hash_=sha256_file(candidate)
        if hash_!=request['candidate_sha256'] or hash_!=summary['candidate_sha256']:raise ValueError('candidate digest differs')
        ids=[c['id'] for c in plan['cases']]
        reports=[read('correlation.json'),read('crosscheck-correlation.json')]
        for report in reports:
            if [c['id'] for c in report['cases']]!=ids or report['candidate_sha256']!=hash_ or report['policy_digest']!=stable_digest(policy):raise ValueError('correlation binding differs')
            if report['source_generation']!=summary['source_generation']:raise ValueError('live source generation differs')
        if reports[0]['experiment_digest']!=stable_digest(plan):raise ValueError('new experiment digest differs')
        fidelity=read('reference-fidelity.json')
        if [c['id'] for c in fidelity['cases']]!=ids:raise ValueError('reference fidelity coverage differs')
        for key,cases in [('public_passed',reports[0]['cases']),('crosscheck_passed',reports[1]['cases']),('reference_fidelity_passed',fidelity['cases'])]:
            if summary[key]!=sum(c['diagnostic_status']=='PASS' for c in cases):raise ValueError('summary counts differ')
        if summary['cases']!=len(ids):raise ValueError('summary coverage differs')
        repeat=read('candidate-repeat.json')
        if repeat['status']!='PASS' or [c['id'] for c in repeat['checks']]!=ids:raise ValueError('candidate repeat coverage differs')
        for cid in ids:
            process=read('xcelium/'+cid+'/process.json')
            if process['returncode'] or process['timed_out']:raise ValueError('candidate EDA incomplete')
        rows.append({'target':target,**pair,'summary':str(path('summary.json')),'summary_sha256':sha256_file(path('summary.json')),'result':summary})
    failed=[]
    for rid in config.get('failed_reference_runs',[]):
        control=Path('.aivw/runs')/rid/'manifest.json';root=Path(json.loads(control.read_text())['details']['payload_root']);pair=verify_split_manifests(control,root/'payload-manifest.json')
        if pair['run_status']=='PASS':raise ValueError('failed history unexpectedly passed')
        failed.append(pair)
    request={'kind':'refined-reference-checkpoint-audit','config_sha256':sha256_file(config_path),'auditor_sha256':sha256_file(Path(__file__)),'inputs':rows,'failed_reference_runs':failed};digest=stable_digest(request)
    run=allocate_split_run(resolve_launch_paths(),Path('/path/to/xh/amsVerify/data/ams').resolve(),library='aivw',cell='refined_reference',view='audit',kind='refined-reference-audit',request_digest=digest)
    summary={'status':'PASS_EVIDENCE_AUDIT','qualification':'NOT_ESTABLISHED','holdout_evaluated':False,'inputs':rows,'failed_reference_runs':failed}
    write_json_once(run.control_root/'request.json',request);write_json_once(run.payload_root/'summary.json',summary)
    publish_split_manifests(run,request_digest=digest,status=summary['status'],control_details={'payload_root':str(run.payload_root)},payload_details={'scope':'evidence_consistency_not_accuracy_qualification'},control_artifacts=[run.control_root/'request.json'],payload_artifacts=[run.payload_root/'summary.json'])
    print(json.dumps({**verify_split_manifests(run.control_manifest,run.payload_manifest),'summary':str(run.payload_root/'summary.json'),'summary_sha256':sha256_file(run.payload_root/'summary.json'),'inputs':rows,'failed_reference_runs':failed}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('config',type=Path);audit(p.parse_args().config)
