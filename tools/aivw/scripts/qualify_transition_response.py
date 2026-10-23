#!/usr/bin/env python3
"""Authenticate, fit, ablate and execute a bounded public transition hypothesis."""
from copy import deepcopy
import argparse
import json
from pathlib import Path
from aivworkbench.executors.trajectory_correlation import correlation_report
from aivworkbench.executors.trajectory_xcelium import execute_candidate
from aivworkbench.manifest import resolve_indexed_artifact, publish_split_manifests, verify_split_manifests
from aivworkbench.plugins.sampled_state_sv import render_model
from aivworkbench.plugins.transition_response import (simulate,fit_surface,fit_tail,
    render_model as render_transition,render_testbench)
from aivworkbench.profiles import load_profile
from aivworkbench.toolchain import capture_module_environment,probe_tools
from aivworkbench.waveform_metrics import read_waveform,compare_trajectories
from aivworkbench.workspace import allocate_split_run,resolve_launch_paths,sha256_file,stable_digest,write_json_once


def load_public(rid):
    control = Path('.aivw/runs')/rid/'manifest.json'
    old = json.loads(control.read_text())
    root = Path(old['details']['payload_root'])
    pair = verify_split_manifests(control, root/'payload-manifest.json')
    def path(relative):
        return resolve_indexed_artifact(control, relative).path
    def read(relative):
        return json.loads(path(relative).read_text())
    model = read('checks/gates/model/model.json')
    plan = read('checks/gates/public_data/experiment.json')
    evidence = read('checks/gates/public_data/characterization-evidence.json')
    parent_report = read('checks/gates/correlation/correlation.json')
    policy = read('checks/gates/correlation/acceptance-policy.json')
    rnm = read('checks/gates/rnm/trajectory-evidence.json')
    parent_source = path('checks/gates/model/candidate.sv')
    identity = dict(model)
    digest = identity.pop('model_digest')
    if (evidence['dataset'] != 'public_calibration' or model['dataset'] != 'public_calibration' or
            stable_digest(identity) != digest or render_model(model) != parent_source.read_text() or
            any(x['experiment_digest'] != stable_digest(plan) for x in (model, evidence, rnm, parent_report)) or
            any(x['source_generation'] != evidence['source_generation'] for x in (model, rnm, parent_report)) or
            any(x['candidate_sha256'] != sha256_file(parent_source) for x in (rnm, parent_report)) or
            rnm['status'] != 'EXECUTED' or parent_report['policy_digest'] != stable_digest(policy)):
        raise ValueError('public parent/source bindings differ')
    ids = [c['id'] for c in plan['cases']]
    if any([c['id'] for c in x['cases']] != ids for x in (evidence, rnm, parent_report)):
        raise ValueError('public case coverage differs')
    signals = {p: 'V' for p in plan['target']['term_order']}
    signals['VPROBE:p'] = 'A'
    output = plan['roles']['output']
    golden, baseline = {}, {}
    for case, source, simulated in zip(plan['cases'], evidence['cases'], rnm['cases']):
        if any(x['case_digest'] != stable_digest(case) for x in (source, simulated)):
            raise ValueError('case digest differs')
        csvs = [a for a in source['artifacts'] if a['path'].endswith('/waveforms.csv')]
        if len(csvs) != 1 or sha256_file(path(csvs[0]['path'])) != csvs[0]['sha256'] or sha256_file(path(simulated['csv'])) != simulated['sha256']:
            raise ValueError('waveform binding differs')
        golden[case['id']] = read_waveform(path(csvs[0]['path']), signals, plan['analysis']['stop_s'])
        baseline[case['id']] = read_waveform(path(simulated['csv']), {output: 'V', 'VPROBE:p': 'A'}, plan['analysis']['stop_s'])
    return model,plan,evidence,golden,baseline,parent_report,policy,pair


def metrics(plan,case,wave,truth,policy):
    return compare_trajectories(wave,truth,output=plan['roles']['output'],current='VPROBE:p',
        events=case['events'],definition=plan['measurement'],policy=policy)


def qualify(recipe_path,target,execute=True):
    recipe=json.loads(recipe_path.read_text())
    model,plan,evidence,golden,baseline,parent_report,policy,pair=load_public(recipe['targets'][target])
    pairs=[pair]
    if 'refined_references' in recipe:
        from correlate_refined_public_matrix import bind,reference,projection
        refined_pair,refined_path=bind(recipe['refined_references'][target])
        refined_plan,refined_evidence,refined_waves,_=reference(refined_path)
        if projection(refined_plan)!=projection(plan) or refined_evidence['source_generation']!=model['source_generation']:
            raise ValueError('refined source/physical experiment differs')
        pairs.append(refined_pair)
        plan,evidence,golden=refined_plan,refined_evidence,refined_waves
    for rid in recipe.get('extra_references',[]):
        from correlate_refined_public_matrix import bind,reference
        extra_pair,extra_path=bind(rid)
        extra_plan,extra_evidence,extra_waves,_=reference(extra_path)
        base_plan={k:v for k,v in plan.items() if k not in ('cases','experiment_id')}
        if (extra_plan['target']!=plan['target'] or
                extra_evidence['source_generation']!=evidence['source_generation'] or
                {k:v for k,v in extra_plan.items() if k not in ('cases','experiment_id')}!=base_plan):
            raise ValueError('extra reference physical experiment differs')
        if {c['id'] for c in plan['cases']} & {c['id'] for c in extra_plan['cases']}:
            raise ValueError('extra reference case ids collide')
        pairs.append(extra_pair)
        plan={**plan,'cases':[*plan['cases'],*extra_plan['cases']]}
        evidence={**evidence,'cases':[*evidence['cases'],*extra_evidence['cases']]}
        golden.update(extra_waves)
    if target=='AON':
        control=Path('.aivw/runs')/recipe['discharge_parent']/'manifest.json'
        root=Path(json.loads(control.read_text())['details']['payload_root'])
        pairs.append(verify_split_manifests(control,root/'payload-manifest.json'))
        discharge=json.loads(resolve_indexed_artifact(control,'model.json').path.read_text())
        if discharge['revision_parent_sha256']!=parent_report['candidate_sha256']:
            raise ValueError('discharge parent candidate differs')
        model=deepcopy(discharge)
    model['transition_response']={k:recipe[k] for k in ('fine_step_s','pre_s','tail_s')}
    for key in ('timeprecision_s','endpoint_step_s','endpoint_window_s'):
        if key in recipe:model['transition_response'][key]=recipe[key]
    if 'fine_scope' in recipe:model['transition_response']['fine_scope']=recipe['fine_scope']
    model['transition_response']['surfaces']=[]
    model['experiment_digest']=stable_digest(plan)
    model['scope']=recipe['scope']
    model['revision_parent_sha256']=parent_report['candidate_sha256']
    package=Path(__file__).resolve().parents[1]
    files=[Path(__file__).resolve(), *[package/'python/aivworkbench'/n for n in (
        'plugins/transition_response.py','plugins/transition_tail.py','plugins/sampled_state.py','plugins/sampled_state_sv.py',
        'plugins/discharge_state.py','plugins/tail_minimax_fit.py','plugins/endpoint_sampling.py','executors/trajectory_xcelium.py','executors/trajectory_correlation.py','waveform_metrics.py')]]
    request={'kind':recipe['kind'],'target':target,'recipe_sha256':sha256_file(recipe_path),
        'sources':pairs,'implementation':{str(p):sha256_file(p) for p in files},'holdout_evaluated':False}
    digest=stable_digest(request)
    run=allocate_split_run(resolve_launch_paths(),Path('/path/to/xh/amsVerify/data/ams').resolve(),
        library=plan['target']['library'],cell=plan['target']['cell'],view='transition_response',
        kind='transition-response',request_digest=digest)
    write_json_once(run.control_root/'request.json',request)
    print(json.dumps({'run_id':run.run_id,'target':target,'payload':str(run.payload_root)}),flush=True)
    summary={'status':'FAIL_EXECUTION','qualification':'NOT_ESTABLISHED','holdout_evaluated':False}
    stages=[]; predicted={}
    try:
        involved=[c for c in plan['cases'] if c['id'].startswith(('startup_','enable_'))]
        def stage(name,cases):
            rows=[]
            for case in cases:
                wave=simulate(model,plan,case); predicted[case['id']]=wave
                result=metrics(plan,case,wave,golden[case['id']],policy)
                rows.append({'id':case['id'],**result})
                print(json.dumps({'stage':name,'case':case['id'],'diagnostic':result['diagnostic_status'],
                    'failed':{k:v['failed_samples'] for k,v in result['waveforms'].items()}}),flush=True)
            stages.append({'stage':name,'model_digest':stable_digest({k:v for k,v in model.items() if k!='model_digest'}),'cases':rows})
        stage('fine_grid_parent',involved)
        if target=='AON':
            for direction in ('rise','fall'):
                model['transition_response']['surfaces'].append(fit_surface(model,plan,golden,
                    cases=recipe['enable_'+direction+'_cases'],axis=1,channel='current',knots=recipe['knots']))
            if recipe.get('enable_output_surface'):
                model['transition_response']['surfaces'].append(fit_surface(model,plan,golden,
                    cases=recipe['enable_rise_cases'],axis=1,channel='output',knots=recipe['knots']))
            if recipe.get('supply_fall_surface'):
                for channel in ('current',):
                    model['transition_response']['surfaces'].append(fit_surface(model,plan,golden,cases=['supply_fall_rate_0','supply_fall_rate_1','supply_fall_rate_2'],axis=0,channel=channel,knots=recipe['knots']))
            stage('enable_current_surface',[c for c in involved if c['id'].startswith('enable_')])
        if target=='AON' and recipe.get('enable_tail'):
            axis=model['axes'][1]
            opts={**recipe['tail'],'axis':1,'rate_scale':axis['slew_scale_per_s'],'supply_min':axis['knots'][-1]*.9}
            model['transition_response']['enable_tail']=fit_tail(model,plan,golden,predicted,recipe['enable_rise_cases'],opts)
            stage('enable_current_tail',[c for c in involved if c['id'].startswith('enable_')])
        for channel in ('output','current'):
            model['transition_response']['surfaces'].append(fit_surface(model,plan,golden,
                cases=recipe['startup_cases'],axis=0,channel=channel,knots=recipe['knots']))
            model['transition_response']['surfaces'][-1]['maximum_input']=model['axes'][0]['knots'][-1]*recipe.get('startup_surface_fraction',.5)
            if channel=='current' and 'endpoint_step_s' in recipe:
                from aivworkbench.plugins.endpoint_sampling import fit_current_endpoints
                model['transition_response']['surfaces'][-1]=fit_current_endpoints(
                    model['transition_response']['surfaces'][-1],plan,golden,
                    recipe['endpoint_step_s'],recipe['endpoint_window_s'])
        startups=[c for c in involved if c['id'].startswith('startup_')]
        stage('startup_response_surface',startups)
        axis=model['axes'][0]
        tail_options={**recipe['tail'],'rate_scale':axis['slew_scale_per_s'],'supply_min':axis['knots'][-1]*.9}
        if recipe.get('reweighted_tail_passes'):
            from aivworkbench.plugins.tail_minimax_fit import fit_row
            rows=[]
            for case in startups:
                row,fit_report=fit_row(model,plan,case,golden[case['id']],predicted[case['id']],tail_options,recipe['reweighted_tail_passes'])
                rows.append(row)
            model['transition_response']['tail']={**tail_options,'by_slew':sorted(rows,key=lambda r:r['rate'])}
        else:
            model['transition_response']['tail']=fit_tail(model,plan,golden,predicted,recipe['startup_cases'],tail_options)
        stage('startup_current_tail',startups)
        model.pop('model_digest',None); model['model_digest']=stable_digest(model)
        write_json_once(run.payload_root/'model.json',model)
        write_json_once(run.payload_root/'experiment.json',plan)
        write_json_once(run.payload_root/'acceptance-policy.json',policy)
        write_json_once(run.payload_root/'ablation.json',{'stages':stages})
        # Full final matrix, including steady and supply/load cases, uses the
        # same closed-loop Python recurrence before independent SV execution.
        for c in plan['cases']:
            if c['id'] not in predicted:predicted[c['id']]=simulate(model,plan,c)
        # Tail can respond to supply steps as well: recompute all non-startup
        # trajectories instead of carrying forward a pre-tail result.
        for c in plan['cases']:
            if c['id'] not in recipe['startup_cases']:predicted[c['id']]=simulate(model,plan,c)
        if execute:
            profile=load_profile('amsverify'); env=capture_module_environment(profile.modules)
            qualified=probe_tools([t for t in profile.tools if t.name=='xrun'],env)
            if len(qualified)!=1 or qualified[0].status!='PASS':raise ValueError('Xcelium unavailable')
            write_json_once(run.payload_root/'toolchain.json',{'tools':[t.to_dict() for t in qualified]})
            source,records=execute_candidate(run.payload_root/'xcelium',render_transition(model),model,plan,
                env,{'xrun':qualified[0].path},1800.,render_testbench)
            actual={r['id']:read_waveform(r['csv'],{plan['roles']['output']:'V','VPROBE:p':'A'},plan['analysis']['stop_s']) for r in records}
            candidate_hash=sha256_file(source)
            parity=[]
            for cid,a in actual.items():
                b=predicted[cid]
                if len(a.times)!=len(b.times) or max(abs(x-y) for x,y in zip(a.times,b.times))>1e-15:raise ValueError('parity axes differ')
                errors={s:max(abs(x-y) for x,y in zip(a.values[s],b.values[s])) for s in a.signals}
                parity.append({'id':cid,'errors':errors,'status':'PASS' if errors[plan['roles']['output']]<1e-8 and errors['VPROBE:p']<1e-10 else 'FAIL'})
            write_json_once(run.payload_root/'parity.json',{'checks':parity})
        else:
            source=run.payload_root/'candidate.sv'; source.write_text(render_transition(model))
            candidate_hash=sha256_file(source); actual=predicted; parity=[]
        report=correlation_report(plan,evidence,golden,actual,policy,candidate_hash)
        write_json_once(run.payload_root/'correlation.json',report)
        parent_by_id={p['id']:p for p in parent_report['cases']}
        rows=[{'id':c['id'],'parent_diagnostic':parent_by_id.get(c['id'],{}).get('diagnostic_status'),
            'diagnostic_status':c['diagnostic_status'],
            'voltage':c['waveforms']['voltage'],'current':c['waveforms']['current']} for c in report['cases']]
        summary={'status':report['status'],'diagnostic_status':report['diagnostic_status'],'qualification':'NOT_ESTABLISHED',
            'candidate_sha256':candidate_hash,'passed_cases':sum(c['diagnostic_status']=='PASS' for c in rows),
            'case_count':len(rows),'cases':rows,'implementation_parity':'PASS' if parity and all(c['status']=='PASS' for c in parity) else 'NOT_PASSED',
            'execution':'XCELIUM' if execute else 'PYTHON_DIAGNOSTIC_ONLY','holdout_evaluated':False}
        if execute and summary['implementation_parity']!='PASS':summary['status']='FAIL_IMPLEMENTATION_PARITY'
        if any(sha256_file(Path(p))!=h for p,h in request['implementation'].items()):raise ValueError('implementation changed during experiment')
        for pair in pairs:
            verify_split_manifests(Path(pair['control_manifest']),Path(pair['payload_manifest']))
    finally:
        write_json_once(run.payload_root/'summary.json',summary)
        publish_split_manifests(run,request_digest=digest,status=summary['status'],
            control_details={'payload_root':str(run.payload_root)},payload_details={'scope':recipe['scope']},
            control_artifacts=[run.control_root/'request.json'],payload_artifacts=[p for p in run.payload_root.rglob('*') if p.is_file()])
        verify_split_manifests(run.control_manifest,run.payload_manifest)
        print(json.dumps({'run_id':run.run_id,**summary}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('recipe',type=Path)
    p.add_argument('--target',choices=['AON','MASTER'],required=True); p.add_argument('--python-only',action='store_true')
    a=p.parse_args(); qualify(a.recipe,a.target,not a.python_only)
