#!/usr/bin/env python3
"""Audit complete public reference fidelity, preserving every native sample."""
import argparse
import json
from pathlib import Path
from correlate_refined_public_matrix import bind, reference, projection
from aivworkbench.manifest import publish_split_manifests, verify_split_manifests
from aivworkbench.waveform_metrics import compare_trajectories
from aivworkbench.workspace import (allocate_split_run, resolve_launch_paths,
    sha256_file, stable_digest, write_json_once)


def audit(config_path, target, policy_path=None):
    config=json.loads(config_path.read_text()); spec=config['targets'][target]
    pairs=[]; paths={}
    for key in ('reference','crosscheck','old_public'):
        pair,path=bind(spec[key]); pairs.append(pair); paths[key]=path
    plan,evidence,waves,logs=reference(paths['reference'])
    other,other_evidence,other_waves,other_logs=reference(paths['crosscheck'])
    old=paths['old_public']
    old_plan=json.loads(old('checks/gates/public_data/experiment.json').read_text())
    old_evidence=json.loads(old('checks/gates/public_data/characterization-evidence.json').read_text())
    if policy_path is not None:
        policy=json.loads(Path(policy_path).read_text());policy_source=str(Path(policy_path).resolve())
    else:
        policy=json.loads(old('checks/gates/correlation/acceptance-policy.json').read_text());policy_source='recorded_run_policy'
    if projection(plan)!=projection(other) or projection(plan)!=projection(old_plan):
        raise ValueError('physical public experiment differs')
    for ev in (evidence,other_evidence):
        if any(ev[k]!=old_evidence[k] for k in ('source_generation','circuit_sha256')):
            raise ValueError('public source identity differs')
    for key in ('reference','crosscheck'):
        path=paths[key]; root=path('checks/gates/characterization/circuit.scs').parent
        for f in (root/'models').rglob('*'):
            if f.is_file() and sha256_file(f)!=sha256_file(old('checks/gates/public_data/'+f.relative_to(root).as_posix())):
                raise ValueError('staged model dependency differs')
    request={'kind':'complete-public-reference-pair-audit','target':target,
        'config_sha256':sha256_file(config_path),'sources':pairs,
        'implementation':{str(Path(p).resolve()):sha256_file(Path(p)) for p in
                          (__file__,str(Path(__file__).with_name('correlate_refined_public_matrix.py')))},
        'policy_digest':stable_digest(policy),'policy_source':policy_source,'holdout_evaluated':False}
    digest=stable_digest(request)
    run=allocate_split_run(resolve_launch_paths(),Path('/path/to/xh/amsVerify/data/ams').resolve(),
        library=plan['target']['library'],cell=plan['target']['cell'],view='reference_pair',
        kind='reference-pair-audit',request_digest=digest)
    write_json_once(run.control_root/'request.json',request)
    rows=[]
    for c in plan['cases']:
        result=compare_trajectories(waves[c['id']],other_waves[c['id']],
            output=plan['roles']['output'],current='VPROBE:p',events=c['events'],
            definition=plan['measurement'],policy=policy)
        rows.append({'id':c['id'],**result})
        print(json.dumps({'run_id':run.run_id,'case':c['id'],'diagnostic':result['diagnostic_status']}),flush=True)
    for pair in pairs:
        if verify_split_manifests(Path(pair['control_manifest']),Path(pair['payload_manifest']))!=pair:
            raise ValueError('reference manifest changed')
    passed=sum(c['diagnostic_status']=='PASS' for c in rows)
    summary={'status':'PASS_EVIDENCE_AUDIT','diagnostic_status':'PASS' if passed==len(rows) else 'FAIL_REFERENCE_FIDELITY',
        'qualification':'NOT_ESTABLISHED','formal_reference_qualified':False,'holdout_evaluated':False,
        'passed_cases':passed,'case_count':len(rows),'source_recheck':'PASS',
        'physical_stimuli_unchanged':True,'identical_repeat':'NOT_PERFORMED',
        'reference_logs':logs,'crosscheck_logs':other_logs,'cases':rows}
    write_json_once(run.payload_root/'summary.json',summary)
    publish_split_manifests(run,request_digest=digest,status=summary['status'],
        control_details={'payload_root':str(run.payload_root)},payload_details={'scope':'public_cross_method_native_axis_fidelity'},
        control_artifacts=[run.control_root/'request.json'],payload_artifacts=[run.payload_root/'summary.json'])
    verify_split_manifests(run.control_manifest,run.payload_manifest)
    print(json.dumps({'run_id':run.run_id,'passed_cases':passed,'case_count':len(rows),'payload':str(run.payload_root)}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config',type=Path); parser.add_argument('--target',choices=['MASTER','AON'],required=True)
    parser.add_argument('--policy',type=Path,help='explicit acceptance policy for a rebind under a new decision')
    args=parser.parse_args(); audit(args.config,args.target,args.policy)
