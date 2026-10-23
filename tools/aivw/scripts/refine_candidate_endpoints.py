#!/usr/bin/env python3
"""Refine candidate endpoint representation without refitting its dynamic modes."""
import argparse
import json
from pathlib import Path
from correlate_refined_public_matrix import bind,reference,projection
from aivworkbench.plugins.transition_response import simulate,render_model,render_testbench
from aivworkbench.plugins.endpoint_sampling import fit_current_endpoints
from aivworkbench.executors.trajectory_xcelium import execute_candidate
from aivworkbench.executors.trajectory_correlation import correlation_report
from aivworkbench.manifest import publish_split_manifests,verify_split_manifests
from aivworkbench.profiles import load_profile
from aivworkbench.toolchain import capture_module_environment,probe_tools
from aivworkbench.waveform_metrics import read_waveform
from aivworkbench.workspace import allocate_split_run,resolve_launch_paths,sha256_file,stable_digest,write_json_once


def run(config_path,python_only=False):
    config=json.loads(config_path.read_text())
    pair,parent=bind(config['parent']);rp,ref=bind(config['reference'])
    plan,evidence,golden,_=reference(ref)
    model=json.loads(parent('model.json').read_text());old_plan=json.loads(parent('experiment.json').read_text())
    old_report=json.loads(parent('correlation.json').read_text());policy=json.loads(parent('acceptance-policy.json').read_text())
    identity=dict(model);identity.pop('model_digest')
    if stable_digest(identity)!=model['model_digest'] or projection(plan)!=projection(old_plan) or model['source_generation']!=evidence['source_generation']:
        raise ValueError('parent/reference binding differs')
    if render_model(model)!=parent('xcelium/candidate.sv').read_text():raise ValueError('parent source rendering differs')
    model['transition_response'].update(config['sampling'])
    surfaces=model['transition_response']['surfaces']
    for i,surface in enumerate(surfaces):
        if 'maximum_input' in surface and 'input_roundoff_v' in config:
            if not 0<config['input_roundoff_v']<=1e-12:raise ValueError('input roundoff guard exceeds 1 pV')
            surface['input_roundoff_v']=config['input_roundoff_v']
        if surface['axis']==0 and surface['direction']==1 and surface['channel']=='current':
            surfaces[i]=fit_current_endpoints(surface,plan,golden,config['sampling']['endpoint_step_s'],config['sampling']['endpoint_window_s'])
    model['revision_parent_sha256']=sha256_file(parent('xcelium/candidate.sv'))
    model['experiment_digest']=stable_digest(plan);model['scope']='public_endpoint_representation_refinement'
    model.pop('model_digest');model['model_digest']=stable_digest(model)
    package=Path(__file__).resolve().parents[1]
    files=[Path(__file__).resolve(),*[package/'python/aivworkbench'/p for p in
        ('plugins/transition_response.py','plugins/endpoint_sampling.py','plugins/transition_tail.py','waveform_metrics.py')]]
    request={'kind':'public-candidate-endpoint-refinement','config_sha256':sha256_file(config_path),'sources':[pair,rp],
        'implementation':{str(p):sha256_file(p) for p in files},'holdout_evaluated':False}
    digest=stable_digest(request);out=allocate_split_run(resolve_launch_paths(),Path('/path/to/xh/amsVerify/data/ams').resolve(),
        library=plan['target']['library'],cell=plan['target']['cell'],view='endpoint_response',kind='endpoint-response',request_digest=digest)
    write_json_once(out.control_root/'request.json',request)
    print(json.dumps({'run_id':out.run_id,'payload':str(out.payload_root)}),flush=True)
    summary={'status':'FAIL_EXECUTION','qualification':'NOT_ESTABLISHED','holdout_evaluated':False}
    try:
        for name,obj in [('model.json',model),('experiment.json',plan),('acceptance-policy.json',policy)]:write_json_once(out.payload_root/name,obj)
        predicted={}
        for case in plan['cases']:
            predicted[case['id']]=simulate(model,plan,case)
            print(json.dumps({'phase':'simulate','case':case['id']}),flush=True)
        if python_only:
            source=out.payload_root/'candidate.sv';source.write_text(render_model(model));actual=predicted;parity=[]
        else:
            profile=load_profile('amsverify');env=capture_module_environment(profile.modules)
            qualified=probe_tools([t for t in profile.tools if t.name=='xrun'],env)
            if len(qualified)!=1 or qualified[0].status!='PASS':raise ValueError('Xcelium unavailable')
            write_json_once(out.payload_root/'toolchain.json',{'tools':[t.to_dict() for t in qualified]})
            source,records=execute_candidate(out.payload_root/'xcelium',render_model(model),model,plan,env,{'xrun':qualified[0].path},1800.,render_testbench)
            actual={r['id']:read_waveform(r['csv'],{plan['roles']['output']:'V','VPROBE:p':'A'},plan['analysis']['stop_s']) for r in records}
            parity=[]
            for cid,a in actual.items():
                b=predicted[cid]
                if len(a.times)!=len(b.times) or max(abs(x-y) for x,y in zip(a.times,b.times))>1e-15:raise ValueError('parity axes differ')
                errors={s:max(abs(x-y) for x,y in zip(a.values[s],b.values[s])) for s in a.signals}
                parity.append({'id':cid,'errors':errors,'status':'PASS' if errors[plan['roles']['output']]<1e-8 and errors['VPROBE:p']<1e-10 else 'FAIL'})
            write_json_once(out.payload_root/'parity.json',{'checks':parity})
        report=correlation_report(plan,evidence,golden,actual,policy,sha256_file(source))
        write_json_once(out.payload_root/'correlation.json',report)
        rows=[{'id':c['id'],'parent_diagnostic':p['diagnostic_status'],'diagnostic_status':c['diagnostic_status'],
               'voltage':c['waveforms']['voltage'],'current':c['waveforms']['current']} for c,p in zip(report['cases'],old_report['cases'])]
        summary={'status':report['status'],'diagnostic_status':report['diagnostic_status'],'qualification':'NOT_ESTABLISHED',
            'candidate_sha256':sha256_file(source),'passed_cases':sum(c['diagnostic_status']=='PASS' for c in rows),
            'case_count':len(rows),'cases':rows,'implementation_parity':'PASS' if parity and all(c['status']=='PASS' for c in parity) else 'NOT_PASSED',
            'execution':'PYTHON_DIAGNOSTIC_ONLY' if python_only else 'XCELIUM','holdout_evaluated':False}
        if not python_only and summary['implementation_parity']!='PASS':summary['status']='FAIL_IMPLEMENTATION_PARITY'
        if any(sha256_file(Path(p))!=h for p,h in request['implementation'].items()):raise ValueError('implementation changed during experiment')
    finally:
        write_json_once(out.payload_root/'summary.json',summary)
        publish_split_manifests(out,request_digest=digest,status=summary['status'],control_details={'payload_root':str(out.payload_root)},
            payload_details={'scope':'public_endpoint_representation_refinement'},control_artifacts=[out.control_root/'request.json'],
            payload_artifacts=[p for p in out.payload_root.rglob('*') if p.is_file()])
        verify_split_manifests(out.control_manifest,out.payload_manifest)
        print(json.dumps({'run_id':out.run_id,'passed_cases':summary.get('passed_cases'),'status':summary['status']}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('config',type=Path)
    parser.add_argument('--python-only',action='store_true');args=parser.parse_args();run(args.config,args.python_only)
