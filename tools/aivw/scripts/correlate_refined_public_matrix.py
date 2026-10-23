#!/usr/bin/env python3
"""Authenticate live-rechecked references and execute unchanged candidate bytes."""
import argparse
import json
from pathlib import Path
from aivworkbench.executors.trajectory_xcelium import execute_candidate
from aivworkbench.executors.trajectory_correlation import correlation_report
from aivworkbench.plugins.transition_response import render_testbench
from aivworkbench.manifest import verify_split_manifests,resolve_indexed_artifact,publish_split_manifests
from aivworkbench.profiles import load_profile
from aivworkbench.toolchain import capture_module_environment,probe_tools
from aivworkbench.waveform_metrics import read_waveform,compare_trajectories
from aivworkbench.workspace import allocate_split_run,resolve_launch_paths,sha256_file,stable_digest,write_json_once


def bind(rid):
    control=Path('.aivw/runs')/rid/'manifest.json';root=Path(json.loads(control.read_text())['details']['payload_root'])
    pair=verify_split_manifests(control,root/'payload-manifest.json')
    return pair,lambda name:resolve_indexed_artifact(control,name).path


from aivworkbench.electrical_experiment import physical_projection as projection  # noqa: E402


def reference(path):
    prefix='checks/gates/characterization/'
    p=path(prefix+'experiment.json');plan=json.loads(p.read_text());evidence=json.loads(path(prefix+'characterization-evidence.json').read_text())
    if (evidence['source_recheck']!='PASS' or evidence['dataset']!='public_calibration' or evidence['experiment_sha256']!=sha256_file(p) or evidence['experiment_digest']!=stable_digest(plan) or evidence['target']!=plan['target']):raise ValueError('live reference provenance differs')
    if [c['id'] for c in evidence['cases']]!=[c['id'] for c in plan['cases']]:raise ValueError('reference case coverage differs')
    if evidence['circuit_sha256']!=sha256_file(path(prefix+'circuit.scs')):raise ValueError('circuit binding differs')
    signals={**{p:'V' for p in plan['target']['term_order']},'VPROBE:p':'A'};waves={};logs=[]
    for case,record in zip(plan['cases'],evidence['cases']):
        if record['case_digest']!=stable_digest(case):raise ValueError('reference case digest differs')
        csv=[a for a in record['artifacts'] if a['path'].endswith('/waveforms.csv')]
        if len(csv)!=1 or sha256_file(path(csv[0]['path']))!=csv[0]['sha256']:raise ValueError('reference CSV binding differs')
        waves[case['id']]=read_waveform(path(csv[0]['path']),signals,plan['analysis']['stop_s'])
        log=path(prefix+'cases/'+case['id']+'/spectre.log').read_text()
        logs.append({'id':case['id'],'trapezoidal_ringing':'Trapezoidal ringing' in log or 'trapezoidal ringing' in log,'samples':len(waves[case['id']].times)})
    return plan,evidence,waves,logs


def run(config_path,target,policy_path=None):
    cfg=json.loads(config_path.read_text());spec=cfg['targets'][target];pairs=[];paths={}
    for k,rid in spec.items():
        pair,path=bind(rid);pairs.append(pair);paths[k]=path
    plan,evidence,golden,logs=reference(paths['reference']);other,other_evidence,check,check_logs=reference(paths['crosscheck'])
    cp=paths['candidate'];model=json.loads(cp('model.json').read_text());old_plan=json.loads(cp('experiment.json').read_text());source=cp('xcelium/candidate.sv')
    if policy_path is not None:
        policy=json.loads(Path(policy_path).read_text());policy_source=str(Path(policy_path).resolve())
    else:
        policy=json.loads(cp('acceptance-policy.json').read_text());policy_source='recorded_candidate_policy'
    identity=dict(model);digest=identity.pop('model_digest')
    if stable_digest(identity)!=digest:raise ValueError('candidate model digest differs')
    if projection(plan)!=projection(other):raise ValueError('physical experiment changed')
    # The candidate plan may carry extra public cases (for example extended
    # startup rates); every reference case must still be present, unchanged.
    if {k:v for k,v in projection(old_plan).items() if k!='cases'}!={k:v for k,v in projection(plan).items() if k!='cases'}:
        raise ValueError('physical experiment changed')
    candidate_cases={c['id']:c for c in old_plan['cases']}
    if any(candidate_cases.get(c['id'])!=c for c in plan['cases']):
        raise ValueError('physical experiment changed')
    if not model['source_generation']==evidence['source_generation']==other_evidence['source_generation']:raise ValueError('live source generation differs from candidate')
    old=paths['old_public'];old_evidence=json.loads(old('checks/gates/public_data/characterization-evidence.json').read_text())
    if evidence['circuit_sha256']!=old_evidence['circuit_sha256'] or other_evidence['circuit_sha256']!=evidence['circuit_sha256']:raise ValueError('live netlist changed from modeled source')
    # Source model dependencies must match independently of staged path spelling.
    for path in (paths['reference'],paths['crosscheck']):
        root=path('checks/gates/characterization/circuit.scs').parent
        for f in (root/'models').rglob('*'):
            if f.is_file():
                rel=f.relative_to(root).as_posix()
                if sha256_file(path('checks/gates/characterization/'+rel))!=sha256_file(old('checks/gates/public_data/'+rel)):raise ValueError('model dependency changed')
    profile=load_profile('amsverify');env=capture_module_environment(profile.modules);qualified=probe_tools([t for t in profile.tools if t.name=='xrun'],env)
    if len(qualified)!=1 or qualified[0].status!='PASS':raise ValueError('Xcelium unavailable')
    request={'kind':cfg['kind'],'target':target,'config_sha256':sha256_file(config_path),'sources':pairs,'candidate_sha256':sha256_file(source),'old_experiment_digest':model['experiment_digest'],'new_experiment_digest':stable_digest(plan),'physical_projection_digest':stable_digest(projection(plan)),'implementation_sha256':sha256_file(Path(__file__)),'policy_digest':stable_digest(policy),'policy_source':policy_source,'holdout_evaluated':False}
    digest=stable_digest(request);run=allocate_split_run(resolve_launch_paths(),Path('/path/to/xh/amsVerify/data/ams').resolve(),library=plan['target']['library'],cell=plan['target']['cell'],view='refined_matrix',kind='refined-matrix',request_digest=digest)
    write_json_once(run.control_root/'request.json',request);print(json.dumps({'run_id':run.run_id,'payload':str(run.payload_root)}),flush=True)
    summary={'status':'FAIL_EXECUTION','qualification':'NOT_ESTABLISHED','holdout_evaluated':False}
    try:
        write_json_once(run.payload_root/'experiment.json',plan);write_json_once(run.payload_root/'acceptance-policy.json',policy);write_json_once(run.payload_root/'toolchain.json',{'tools':[t.to_dict() for t in qualified]})
        fidelity=[]
        for c in plan['cases']:
            result=compare_trajectories(golden[c['id']],check[c['id']],output=plan['roles']['output'],current='VPROBE:p',events=c['events'],definition=plan['measurement'],policy=policy)
            fidelity.append({'id':c['id'],**result});print(json.dumps({'phase':'reference_fidelity','case':c['id'],'diagnostic_status':result['diagnostic_status']}),flush=True)
        write_json_once(run.payload_root/'reference-fidelity.json',{'cases':fidelity,'logs':logs,'crosscheck_logs':check_logs,'identical_repeat':'NOT_PERFORMED','scope':'complete_public_matrix_cross_method_step_check'})
        executed,records=execute_candidate(run.payload_root/'xcelium',source.read_text(),model,plan,env,{'xrun':qualified[0].path},1800.,render_testbench)
        if sha256_file(executed)!=sha256_file(source):raise ValueError('candidate bytes changed')
        signals={plan['roles']['output']:'V','VPROBE:p':'A'}
        actual={r['id']:read_waveform(r['csv'],signals,plan['analysis']['stop_s']) for r in records}
        exact=[]
        for c in plan['cases']:
            previous=read_waveform(cp('xcelium/'+c['id']+'/waveforms.csv'),signals,plan['analysis']['stop_s']);a=actual[c['id']]
            if a.times!=previous.times:raise ValueError('candidate physical axes changed')
            errors={s:max(abs(x-y) for x,y in zip(a.values[s],previous.values[s])) for s in signals}
            exact.append({'id':c['id'],'max_error':errors})
            if errors[plan['roles']['output']]>1e-9 or errors['VPROBE:p']>1e-10:raise ValueError('unchanged candidate execution differs')
        write_json_once(run.payload_root/'candidate-repeat.json',{'checks':exact,'status':'PASS','scope':'same_candidate_and_physical_stimuli'})
        report=correlation_report(plan,evidence,golden,actual,policy,sha256_file(executed));cross=correlation_report(other,other_evidence,check,actual,policy,sha256_file(executed))
        write_json_once(run.payload_root/'correlation.json',report);write_json_once(run.payload_root/'crosscheck-correlation.json',cross)
        if report['status']==cross['status']:
            status=report['status']
        else:
            status='FAIL_CORRELATION'
        summary={'status':status,'qualification':'NOT_ESTABLISHED','holdout_evaluated':False,'candidate_sha256':sha256_file(executed),'source_generation':evidence['source_generation'],'live_source_recheck':'PASS','physical_stimuli_unchanged':True,'candidate_repeat':'PASS',
            'cases':len(plan['cases']),'reference_fidelity_passed':sum(c['diagnostic_status']=='PASS' for c in fidelity),'public_passed':sum(c['diagnostic_status']=='PASS' for c in report['cases']),'crosscheck_passed':sum(c['diagnostic_status']=='PASS' for c in cross['cases']),
            'reference_fidelity_failures':[c['id'] for c in fidelity if c['diagnostic_status']!='PASS'],'candidate_failures':[c['id'] for c in report['cases'] if c['diagnostic_status']!='PASS'],'reference_trapezoidal_ringing_cases':[c['id'] for c in logs if c['trapezoidal_ringing']],
            'formal_reference_qualified':False,'final_freeze':False}
        for pair in pairs:verify_split_manifests(Path(pair['control_manifest']),Path(pair['payload_manifest']))
    finally:
        write_json_once(run.payload_root/'summary.json',summary);publish_split_manifests(run,request_digest=digest,status=summary['status'],control_details={'payload_root':str(run.payload_root)},payload_details={'scope':'full_public_reference_and_fixed_candidate_correlation'},control_artifacts=[run.control_root/'request.json'],payload_artifacts=[p for p in run.payload_root.rglob('*') if p.is_file()]);verify_split_manifests(run.control_manifest,run.payload_manifest)
        print(json.dumps({'run_id':run.run_id,**summary}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('config',type=Path);p.add_argument('--target',choices=['MASTER','AON'],required=True);p.add_argument('--policy',type=Path,help='explicit acceptance policy for a rebind under a new decision');a=p.parse_args();run(a.config,a.target,a.policy)
