#!/usr/bin/env python3
"""Bind startup fidelity and tail refit evidence while preserving scope limits."""
import argparse
import json
from pathlib import Path
from aivworkbench.manifest import verify_split_manifests,resolve_indexed_artifact,publish_split_manifests
from aivworkbench.workspace import allocate_split_run,resolve_launch_paths,sha256_file,stable_digest,write_json_once


def audit(config_path):
    config=json.loads(config_path.read_text());rows=[]
    for label,rid in config['runs'].items():
        control=Path('.aivw/runs')/rid/'manifest.json';root=Path(json.loads(control.read_text())['details']['payload_root'])
        pair=verify_split_manifests(control,root/'payload-manifest.json')
        def path(name):return resolve_indexed_artifact(control,name).path
        def read(name):return json.loads(path(name).read_text())
        summary=read('summary.json');request=json.loads((control.parent/'request.json').read_text())
        if stable_digest(request)!=pair['request_digest'] or summary['qualification']!='NOT_ESTABLISHED' or summary['holdout_evaluated'] is not False:raise ValueError('request/scope boundary differs')
        parents=request.get('sources',[request.get('parent')])
        for parent in parents:
            if verify_split_manifests(Path(parent['control_manifest']),Path(parent['payload_manifest']))!=parent:raise ValueError('parent binding changed')
        if label.startswith('refit'):
            if summary['status']!='BLOCKED_CONTRACT' or summary['implementation_parity']!='PASS' or summary['formal_reference_promoted'] is not False:raise ValueError('refit scope differs')
            model=read('model.json');identity=dict(model);digest=identity.pop('model_digest')
            plan=read('experiment.json');policy=read('acceptance-policy.json');historical=read('historical-correlation.json');parity=read('parity.json')['checks']
            source=path('xcelium/candidate.sv')
            if stable_digest(identity)!=digest or model['experiment_digest']!=stable_digest(plan):raise ValueError('model experiment binding differs')
            if historical['candidate_sha256']!=sha256_file(source) or summary['candidate_sha256']!=sha256_file(source) or historical['policy_digest']!=stable_digest(policy):raise ValueError('candidate policy binding differs')
            ids=[c['id'] for c in plan['cases']]
            if [c['id'] for c in historical['cases']]!=ids or [c['id'] for c in parity]!=ids or any(c['status']!='PASS' for c in parity):raise ValueError('public execution coverage differs')
            if summary['historical_public_passed']!=sum(c['diagnostic_status']=='PASS' for c in historical['cases']):raise ValueError('historical count differs')
            non_target=read('non-target-regression.json')
            if non_target['status']!='PASS' or len(non_target['checks'])!=len(ids)-1:raise ValueError('non-target regression evidence missing')
            for cid in ids:
                process=read('xcelium/'+cid+'/process.json')
                if process['returncode']!=0 or process['timed_out']:raise ValueError('incomplete Xcelium case')
            tails=read('refined-tail-correlation.json')['reports']
            expected='PASS' if all(t['refitted']['diagnostic_status']=='PASS' for t in tails) else 'FAIL'
            if summary['refined_tail_diagnostic']!=expected:raise ValueError('refined tail summary differs')
        elif label=='failed_fidelity':
            if summary['status']!='FAIL_EXECUTION':raise ValueError('failed run status changed')
        else:
            if summary['status']!='PUBLIC_FIDELITY_DIAGNOSTICS_RECORDED':raise ValueError('fidelity incomplete')
            for case in summary['cases']:
                csv=path('cases/'+case['id']+'/waveforms.csv')
                if sha256_file(csv)!=case['csv_sha256']:raise ValueError('fidelity CSV binding differs')
                for tool in ('spectre','ocean'):
                    process=read('cases/'+case['id']+'/'+tool+'.process.json')
                    if process['returncode']!=0 or process['timed_out']:raise ValueError('fidelity process incomplete')
            if label=='converged_fidelity' and any(c['diagnostic_status']!='PASS' for c in summary['comparisons'][1:]):raise ValueError('fidelity convergence failed')
        rows.append({'label':label,**pair,'summary':str(path('summary.json')),'summary_sha256':sha256_file(path('summary.json')),'result':summary})
    request={'kind':'startup-tail-evidence-audit','config_sha256':sha256_file(config_path),'auditor_sha256':sha256_file(Path(__file__)),'inputs':rows}
    digest=stable_digest(request);run=allocate_split_run(resolve_launch_paths(),Path('/path/to/xh/amsVerify/data/ams').resolve(),library='aivw',cell='startup_tail',view='audit',kind='startup-tail-audit',request_digest=digest)
    summary={'status':'PASS_EVIDENCE_AUDIT','qualification':'NOT_ESTABLISHED','holdout_evaluated':False,'inputs':rows}
    write_json_once(run.control_root/'request.json',request);write_json_once(run.payload_root/'summary.json',summary)
    publish_split_manifests(run,request_digest=digest,status=summary['status'],control_details={'payload_root':str(run.payload_root)},payload_details={'scope':'evidence_consistency_not_full_dynamic_qualification'},control_artifacts=[run.control_root/'request.json'],payload_artifacts=[run.payload_root/'summary.json'])
    print(json.dumps({**verify_split_manifests(run.control_manifest,run.payload_manifest),'summary':str(run.payload_root/'summary.json'),'summary_sha256':sha256_file(run.payload_root/'summary.json'),'inputs':rows}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('config',type=Path);audit(p.parse_args().config)
