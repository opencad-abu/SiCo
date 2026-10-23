#!/usr/bin/env python3
"""Authenticate completed public transition experiments without hidden access."""
import argparse
import json
from pathlib import Path
from aivworkbench.manifest import resolve_indexed_artifact,verify_split_manifests,publish_split_manifests
from aivworkbench.workspace import allocate_split_run,resolve_launch_paths,sha256_file,stable_digest,write_json_once


def audit(config_path):
    config=json.loads(config_path.read_text()); rows=[]
    for label,rid in config['runs'].items():
        control=Path('.aivw/runs')/rid/'manifest.json'
        root=Path(json.loads(control.read_text())['details']['payload_root'])
        pair=verify_split_manifests(control,root/'payload-manifest.json')
        def path(name):return resolve_indexed_artifact(control,name).path
        def read(name):return json.loads(path(name).read_text())
        summary=read('summary.json');report=read('correlation.json');model=read('model.json');plan=read('experiment.json');policy=read('acceptance-policy.json')
        request=json.loads((control.parent/'request.json').read_text())
        if stable_digest(request)!=pair['request_digest']:raise ValueError('request identity differs')
        source=path('xcelium/candidate.sv');parity=read('parity.json')['checks'];ids=[c['id'] for c in plan['cases']]
        identity=dict(model);model_digest=identity.pop('model_digest')
        if stable_digest(identity)!=model_digest or model['experiment_digest']!=stable_digest(plan):raise ValueError('model identity differs')
        if report['policy_digest']!=stable_digest(policy) or report['experiment_digest']!=stable_digest(plan):raise ValueError('correlation contract differs')
        if report['source_generation']!=model['source_generation']:raise ValueError('source generation differs')
        if summary['candidate_sha256']!=sha256_file(source) or report['candidate_sha256']!=sha256_file(source):raise ValueError('candidate bytes differ')
        if any([c['id'] for c in collection]!=ids for collection in [summary['cases'],report['cases'],parity]):raise ValueError('complete public coverage differs')
        for stored,measured in zip(summary['cases'],report['cases']):
            if (stored['diagnostic_status']!=measured['diagnostic_status'] or
                    stored['voltage']!=measured['waveforms']['voltage'] or
                    stored['current']!=measured['waveforms']['current']):
                raise ValueError('case summary differs from measured report')
        if summary['execution']!='XCELIUM' or summary['implementation_parity']!='PASS' or any(c['status']!='PASS' for c in parity):raise ValueError('real execution parity missing')
        if summary['qualification']!='NOT_ESTABLISHED' or summary['holdout_evaluated'] is not False or summary['status']!='BLOCKED_CONTRACT':raise ValueError('public qualification boundary differs')
        if report['status']!=summary['status'] or report['diagnostic_status']!=summary['diagnostic_status']:
            raise ValueError('aggregate report status differs')
        toolchain=read('toolchain.json')['tools']
        if len(toolchain)!=1 or toolchain[0]['status']!='PASS':raise ValueError('qualified Xcelium evidence missing')
        for src in request['sources']:
            checked=verify_split_manifests(Path(src['control_manifest']),Path(src['payload_manifest']))
            if checked!=src:raise ValueError('authenticated parent changed')
        for cid in ids:
            process=read('xcelium/'+cid+'/process.json')
            if process['returncode']!=0 or process['timed_out']:raise ValueError('incomplete EDA process')
        passed=sum(c['diagnostic_status']=='PASS' for c in report['cases'])
        if summary['passed_cases']!=passed or summary['case_count']!=len(ids):raise ValueError('summary count differs')
        rows.append({'label':label,**pair,'summary':str(path('summary.json')),'summary_sha256':sha256_file(path('summary.json')),
            'candidate_sha256':sha256_file(source),'passed_cases':passed,'case_count':len(ids),
            'parity':'PASS','regressions_from_v6':[c['id'] for c in summary['cases'] if c['parent_diagnostic']=='PASS' and c['diagnostic_status']!='PASS'],
            'remaining_failures':[c['id'] for c in summary['cases'] if c['diagnostic_status']!='PASS']})
    request={'kind':'public-transition-evidence-audit','config_sha256':sha256_file(config_path),'auditor_sha256':sha256_file(Path(__file__)),'inputs':rows}
    digest=stable_digest(request)
    run=allocate_split_run(resolve_launch_paths(),Path('/path/to/xh/amsVerify/data/ams').resolve(),library='aivw',cell='transition_checkpoint',view='evidence',kind='transition-audit',request_digest=digest)
    summary={'status':'PASS_EVIDENCE_AUDIT','qualification':'NOT_ESTABLISHED','holdout_evaluated':False,'inputs':rows}
    write_json_once(run.control_root/'request.json',request);write_json_once(run.payload_root/'summary.json',summary)
    publish_split_manifests(run,request_digest=digest,status=summary['status'],control_details={'payload_root':str(run.payload_root)},
        payload_details={'scope':'public_transition_evidence_consistency_only'},control_artifacts=[run.control_root/'request.json'],payload_artifacts=[run.payload_root/'summary.json'])
    result=verify_split_manifests(run.control_manifest,run.payload_manifest)
    print(json.dumps({**result,'summary':str(run.payload_root/'summary.json'),'summary_sha256':sha256_file(run.payload_root/'summary.json'),'inputs':rows}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('config',type=Path);audit(p.parse_args().config)
