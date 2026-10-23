#!/usr/bin/env python3
"""Refit only the fast slew tail row; retain historical full-matrix diagnostics."""
from copy import deepcopy
import argparse
import json
from pathlib import Path
from qualify_transition_response import load_public
from audit_startup_tail_fidelity import window
from aivworkbench.plugins.transition_response import simulate,fit_tail,render_model,render_testbench
from aivworkbench.executors.trajectory_xcelium import execute_candidate
from aivworkbench.executors.trajectory_correlation import correlation_report
from aivworkbench.manifest import resolve_indexed_artifact,verify_split_manifests,publish_split_manifests
from aivworkbench.profiles import load_profile
from aivworkbench.toolchain import capture_module_environment,probe_tools
from aivworkbench.waveform_metrics import read_waveform,compare_trajectories
from aivworkbench.workspace import allocate_split_run,resolve_launch_paths,sha256_file,stable_digest,write_json_once


def refit(recipe_path):
    recipe=json.loads(recipe_path.read_text())
    _,plan,evidence,golden,_,_,policy,public_pair=load_public(recipe['public_parent'])
    pairs=[public_pair]
    def bind(rid):
        control=Path('.aivw/runs')/rid/'manifest.json';root=Path(json.loads(control.read_text())['details']['payload_root'])
        pairs.append(verify_split_manifests(control,root/'payload-manifest.json'))
        return lambda name:resolve_indexed_artifact(control,name).path
    parent=bind(recipe['parent_run']);fidelity=bind(recipe['fidelity_run'])
    model=json.loads(parent('model.json').read_text());identity=dict(model);identity.pop('model_digest')
    if stable_digest(identity)!=model['model_digest'] or model['experiment_digest']!=stable_digest(plan) or render_model(model)!=parent('xcelium/candidate.sv').read_text():raise ValueError('parent model identity differs')
    fid=json.loads(fidelity('summary.json').read_text())
    if fid['status']!='PUBLIC_FIDELITY_DIAGNOSTICS_RECORDED' or not all(c['diagnostic_status']=='PASS' for c in fid['comparisons'][1:]):raise ValueError('reference numerical convergence not established')
    fid_request=json.loads((Path(pairs[-1]['control_manifest']).parent/'request.json').read_text())
    if (fid_request['parent']!=public_pair or fid_request['source_generation']!=evidence['source_generation'] or
            stable_digest(fid_request)!=pairs[-1]['request_digest']):raise ValueError('fidelity source provenance differs')
    signals={**{p:'V' for p in plan['target']['term_order']},'VPROBE:p':'A'}
    reference=read_waveform(fidelity(recipe['training_csv']),signals,recipe['reference_stop_s'])
    checks={p:read_waveform(fidelity(p),signals,recipe['reference_stop_s']) for p in recipe['validation_csvs']}
    case=next(c for c in plan['cases'] if c['id']==recipe['case']);out=plan['roles']['output']
    original_tail=deepcopy(model['transition_response']['tail'])
    base=deepcopy(model);base['transition_response'].pop('tail')
    baseline=simulate(base,plan,case)
    fit_report={'method':'original_least_squares'}
    if recipe.get('reweighted_passes'):
        from aivworkbench.plugins.tail_minimax_fit import fit_row
        fast,fit_report=fit_row(base,plan,case,reference,baseline,original_tail,recipe['reweighted_passes'])
    else:
        fitted=fit_tail(base,plan,{case['id']:reference},{case['id']:baseline},[case['id']],original_tail)
        if len(fitted['by_slew'])!=1:raise ValueError('one slew row required')
        fast=fitted['by_slew'][0]
    old=original_tail['by_slew'][-1]
    if abs(fast['rate']/old['rate']-1)>1e-7:raise ValueError('fast tail slew binding differs')
    model['transition_response']['tail']['by_slew'][-1]=fast
    model['transition_response']['tail']['coefficient_provenance']={'fast_slew_fidelity_run':recipe['fidelity_run'],'other_slews_parent':recipe['parent_run']}
    model['revision_parent_sha256']=sha256_file(parent('xcelium/candidate.sv'))
    model['scope']=recipe['scope'];model.pop('model_digest');model['model_digest']=stable_digest(model)
    before=deepcopy(original_tail);after=deepcopy(model['transition_response']['tail'])
    after.pop('coefficient_provenance');before['by_slew'][-1]=after['by_slew'][-1]
    if before!=after:raise ValueError('unrelated tail parameters changed')
    request={'kind':recipe['kind'],'recipe_sha256':sha256_file(recipe_path),'sources':pairs,
        'candidate_digest':model['model_digest'],'holdout_evaluated':False,'source_qualification':'immutable_snapshot_only',
        'implementation':{str(p):sha256_file(p) for p in [Path(__file__).resolve(),Path(__file__).resolve().parents[1]/'python/aivworkbench/plugins/transition_tail.py',Path(__file__).resolve().parents[1]/'python/aivworkbench/plugins/transition_response.py',Path(__file__).resolve().parents[1]/'python/aivworkbench/plugins/tail_minimax_fit.py']}}
    digest=stable_digest(request)
    run=allocate_split_run(resolve_launch_paths(),Path('/path/to/xh/amsVerify/data/ams').resolve(),library='amsLDO',cell='LDO_MASTER',view='tail_refit',kind='tail-refit',request_digest=digest)
    write_json_once(run.control_root/'request.json',request)
    print(json.dumps({'run_id':run.run_id,'payload':str(run.payload_root)}),flush=True)
    summary={'status':'FAIL_EXECUTION','qualification':'NOT_ESTABLISHED','holdout_evaluated':False}
    try:
        write_json_once(run.payload_root/'fit-report.json',fit_report)
        write_json_once(run.payload_root/'model.json',model);write_json_once(run.payload_root/'experiment.json',plan);write_json_once(run.payload_root/'acceptance-policy.json',policy)
        predicted={c['id']:simulate(model,plan,c) for c in plan['cases']}
        profile=load_profile('amsverify');env=capture_module_environment(profile.modules);qualified=probe_tools([t for t in profile.tools if t.name=='xrun'],env)
        if len(qualified)!=1 or qualified[0].status!='PASS':raise ValueError('qualified Xcelium unavailable')
        write_json_once(run.payload_root/'toolchain.json',{'tools':[t.to_dict() for t in qualified]})
        source,records=execute_candidate(run.payload_root/'xcelium',render_model(model),model,plan,env,{'xrun':qualified[0].path},1200.,render_testbench)
        actual={r['id']:read_waveform(r['csv'],{out:'V','VPROBE:p':'A'},plan['analysis']['stop_s']) for r in records}
        parity=[]
        for cid,a in actual.items():
            b=predicted[cid]
            if len(a.times)!=len(b.times) or max(abs(x-y) for x,y in zip(a.times,b.times))>1e-15:raise ValueError('implementation time axes differ')
            errors={s:max(abs(x-y) for x,y in zip(a.values[s],b.values[s])) for s in a.signals}
            parity.append({'id':cid,'max_error':errors,'status':'PASS' if errors[out]<1e-8 and errors['VPROBE:p']<1e-10 else 'FAIL'})
        write_json_once(run.payload_root/'parity.json',{'checks':parity})
        old_report=correlation_report(plan,evidence,golden,actual,policy,sha256_file(source))
        write_json_once(run.payload_root/'historical-correlation.json',old_report)
        non_target=[]
        for c in plan['cases']:
            if c['id']==case['id']:continue
            old_csv=read_waveform(parent('xcelium/'+c['id']+'/waveforms.csv'),{out:'V','VPROBE:p':'A'},plan['analysis']['stop_s'])
            current=actual[c['id']]
            if old_csv.times!=current.times:raise ValueError('non-target axes changed')
            errors={s:max(abs(x-y) for x,y in zip(old_csv.values[s],current.values[s])) for s in current.signals}
            non_target.append({'id':c['id'],'max_error':errors})
            if errors[out]>1e-9 or errors['VPROBE:p']>1e-10:raise ValueError('non-target waveform regression')
        write_json_once(run.payload_root/'non-target-regression.json',{'checks':non_target,'status':'PASS'})
        tail_reports=[]
        old_wave=read_waveform(parent('xcelium/'+case['id']+'/waveforms.csv'),{out:'V','VPROBE:p':'A'},plan['analysis']['stop_s'])
        for label,w in [('training',reference),*checks.items()]:
            truth=window(w,*recipe['window_s'])
            result=compare_trajectories(window(actual[case['id']],*recipe['window_s']),truth,output=out,current='VPROBE:p',events=[],definition=plan['measurement'],policy=policy)
            prior=compare_trajectories(window(old_wave,*recipe['window_s']),truth,output=out,current='VPROBE:p',events=[],definition=plan['measurement'],policy=policy)
            tail_reports.append({'reference':label,'parent':prior,'refitted':result})
        write_json_once(run.payload_root/'refined-tail-correlation.json',{'scope':recipe['scope'],'window_s':recipe['window_s'],'reports':tail_reports})
        summary={'status':'BLOCKED_CONTRACT','qualification':'NOT_ESTABLISHED','holdout_evaluated':False,'candidate_sha256':sha256_file(source),
            'historical_public_passed':sum(c['diagnostic_status']=='PASS' for c in old_report['cases']),'historical_public_count':len(plan['cases']),
            'refined_tail_diagnostic':'PASS' if all(c['refitted']['diagnostic_status']=='PASS' for c in tail_reports) else 'FAIL',
            'implementation_parity':'PASS' if all(c['status']=='PASS' for c in parity) else 'FAIL',
            'unchanged_slow_slew_coefficients':True,'pole_count':len(original_tail['poles']),
            'formal_reference_promoted':False,'scope':recipe['scope']}
        if summary['implementation_parity']!='PASS':summary['status']='FAIL_IMPLEMENTATION_PARITY'
        for pair in pairs:verify_split_manifests(Path(pair['control_manifest']),Path(pair['payload_manifest']))
        if any(sha256_file(Path(p))!=h for p,h in request['implementation'].items()):raise ValueError('implementation changed during run')
    finally:
        write_json_once(run.payload_root/'summary.json',summary)
        publish_split_manifests(run,request_digest=digest,status=summary['status'],control_details={'payload_root':str(run.payload_root)},payload_details={'scope':recipe['scope']},control_artifacts=[run.control_root/'request.json'],payload_artifacts=[p for p in run.payload_root.rglob('*') if p.is_file()])
        verify_split_manifests(run.control_manifest,run.payload_manifest)
        print(json.dumps({'run_id':run.run_id,**summary}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('recipe',type=Path);refit(p.parse_args().recipe)
