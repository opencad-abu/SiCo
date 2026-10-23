#!/usr/bin/env python3
"""Re-execute an authenticated public circuit snapshot to test tail fidelity."""
import argparse
import json
import re
from pathlib import Path
from aivworkbench.electrical_experiment import ocean_export
from aivworkbench.executors.ldo_structure_common import _CHILD_ENV
from aivworkbench.manifest import verify_split_manifests,resolve_indexed_artifact,publish_split_manifests
from aivworkbench.process import run_process_group
from aivworkbench.profiles import load_profile
from aivworkbench.toolchain import capture_module_environment,probe_tools
from aivworkbench.waveform_metrics import Waveform,read_waveform,compare_trajectories
from aivworkbench.workspace import allocate_split_run,resolve_launch_paths,sha256_file,stable_digest,write_json_once


def window(w,start,stop):
    times=(start,*[t for t in w.times if start<t<stop],stop)
    return Waveform(times,w.signals,{s:tuple(w.sample(s,t) for t in times) for s in w.signals})


def deck_variant(text,spec,start,stop):
    if not 0<spec['maxstep']<=2e-7 or not start+spec['ramp_s']<stop or stop/spec['maxstep']>200000:
        raise ValueError('fidelity experiment budget invalid')
    # Source topology/model includes unchanged; only independent supply stimulus,
    # analysis duration and named numerical controls change in this diagnostic.
    supply=re.search(r'^VS_VDD .*$',text,re.M)
    if not supply:raise ValueError('public supply source missing')
    line='VS_VDD (aivw_internal_supply 0) vsource type=pwl wave=[ 0 0 %.17g 0 %.17g 5 %.17g 5 ]'%(start,start+spec['ramp_s'],stop)
    text=text[:supply.start()]+line+text[supply.end():]
    text,count=re.subn(r'^simulatorOptions options .*$',
        'simulatorOptions options temp=27 reltol=%.17g vabstol=%.17g iabstol=%.17g'%(spec['reltol'],spec['vabstol'],spec['iabstol']),text,flags=re.M)
    if count!=1:raise ValueError('public options missing')
    text,count=re.subn(r'^tran tran .*$',
        'tran tran stop=%.17g errpreset=%s maxstep=%.17g'%(stop,spec['errpreset'],spec['maxstep']),text,flags=re.M)
    if count!=1:raise ValueError('public transient analysis missing')
    if 'method' in spec:
        if spec['method'] not in ('trap','gear2only','traponly'):raise ValueError('unsupported integration method')
        text=re.sub(r'^(tran tran .*)$',lambda m:m[1]+' method='+spec['method'],text,flags=re.M)
    return text


def audit(recipe_path):
    recipe=json.loads(recipe_path.read_text());control=Path('.aivw/runs')/recipe['parent_run']/'manifest.json'
    root=Path(json.loads(control.read_text())['details']['payload_root'])
    pair=verify_split_manifests(control,root/'payload-manifest.json')
    prefix='checks/gates/public_data/'
    def path(name):return resolve_indexed_artifact(control,prefix+name).path
    plan=json.loads(path('experiment.json').read_text());evidence=json.loads(path('characterization-evidence.json').read_text())
    if evidence['dataset']!='public_calibration' or evidence['experiment_digest']!=stable_digest(plan):raise ValueError('public experiment binding differs')
    case=next(c for c in plan['cases'] if c['id']==recipe['case']);record=next(c for c in evidence['cases'] if c['id']==case['id'])
    if record['case_digest']!=stable_digest(case):raise ValueError('public source case differs')
    start=case['events'][0]['start_s'];stop=recipe['stop_s'];output=plan['roles']['output']
    signals={**{p:'V' for p in plan['target']['term_order']},'VPROBE:p':'A'}
    original=read_waveform(path('cases/'+case['id']+'/waveforms.csv'),signals,plan['analysis']['stop_s'])
    policy=json.loads(resolve_indexed_artifact(control,'checks/gates/correlation/acceptance-policy.json').path.read_text())
    profile=load_profile('amsverify');env=capture_module_environment(profile.modules)
    qualified=probe_tools([t for t in profile.tools if t.name in ('spectre','ocean')],env)
    if len(qualified)!=2 or any(t.status!='PASS' for t in qualified):raise ValueError('qualified simulator/exporter missing')
    tools={t.name:t.path for t in qualified};env={k:v for k,v in env.items() if k in _CHILD_ENV}
    request={'kind':recipe['kind'],'recipe_sha256':sha256_file(recipe_path),'parent':pair,'tools':[t.to_dict() for t in qualified],
        'implementation_sha256':sha256_file(Path(__file__)),'source_generation':evidence['source_generation'],'live_source_rechecked':False,'holdout_evaluated':False}
    digest=stable_digest(request)
    run=allocate_split_run(resolve_launch_paths(),Path('/path/to/xh/amsVerify/data/ams').resolve(),library='amsLDO',cell='LDO_MASTER',view='startup_fidelity',kind='startup-fidelity',request_digest=digest)
    write_json_once(run.control_root/'request.json',request)
    print(json.dumps({'run_id':run.run_id,'payload':str(run.payload_root)}),flush=True)
    summary={'status':'FAIL_EXECUTION','qualification':'NOT_ESTABLISHED','holdout_evaluated':False}
    try:
        # Resolve and authenticate every copied dependency separately.
        files=[path('circuit.scs'),*sorted((path('circuit.scs').parent/'models').rglob('*'))]
        staged=[]
        for source in files:
            if not source.is_file():continue
            relative=source.relative_to(path('circuit.scs').parent).as_posix()
            source=path(relative);dest=run.payload_root/relative;dest.parent.mkdir(parents=True,exist_ok=True)
            with dest.open('xb') as stream:stream.write(source.read_bytes())
            staged.append({'path':relative,'sha256':sha256_file(dest)})
        write_json_once(run.payload_root/'staged-source.json',{'files':staged})
        waves={};records=[]
        template=path('cases/'+case['id']+'/input.scs').read_text()
        for spec in recipe['cases']:
            local=run.payload_root/'cases'/spec['id'];local.mkdir(parents=True)
            deck=local/'input.scs';deck.write_text(deck_variant(template,spec,start,stop))
            psf=local/'psf'
            def execute(name,args):
                process=run_process_group((tools[name],*map(str,args)),cwd=local,environment=env,log_file=local/(name+'.console.log'),timeout=600.)
                write_json_once(local/(name+'.process.json'),process.to_dict())
                if process.returncode or process.timed_out:raise ValueError(name+' failed; inspect isolated logs')
            execute('spectre',(deck,'-format','psfascii','-raw',psf,'+log',local/'spectre.log'))
            log=(local/'spectre.log').read_text()
            if not re.search('spectre completes with 0 errors',log,re.I):raise ValueError('Spectre completion absent')
            script=local/'export.ocn';csv=local/'waveforms.csv';script.write_text(ocean_export(psf,csv,signals))
            execute('ocean',('-nograph','-nocdsinit','-cdslib','/path/to/xh/amsVerify/cds.lib','-log',local/'ocean.log','-replay',script))
            if 'AIVW_WAVEFORM_EXPORT_COMPLETE' not in (local/'ocean.log').read_text():raise ValueError('export completion absent')
            waves[spec['id']]=read_waveform(csv,signals,stop)
            records.append({'id':spec['id'],'spec':spec,'csv_sha256':sha256_file(csv),'samples':len(waves[spec['id']].times)})
            print(json.dumps(records[-1]),flush=True)
        comparisons=[]
        for a,b in recipe.get('comparisons',[('moderate_replay','original'),('conservative_5ns','moderate_replay'),('conservative_2ns','conservative_5ns'),('conservative_2ns_repeat','conservative_2ns')]):
            wa=window(waves[a],*recipe['window_s']);wb=window(original if b=='original' else waves[b],*recipe['window_s'])
            report=compare_trajectories(wa,wb,output=output,current='VPROBE:p',events=[],definition=plan['measurement'],policy=policy)
            comparisons.append({'candidate':a,'reference':b,**report})
        summary={'status':'PUBLIC_FIDELITY_DIAGNOSTICS_RECORDED','qualification':'NOT_ESTABLISHED','holdout_evaluated':False,
            'scope':recipe['scope'],'cases':records,'window_s':recipe['window_s'],'comparisons':comparisons,
            'repeat_csv_equal':None if recipe.get('repeat_pair','default') is None else records[2]['csv_sha256']==records[3]['csv_sha256'],'live_source_rechecked':False,
            'interpretation':'numerical_sensitivity_of_public_snapshot_not_candidate_qualification'}
        if sha256_file(Path(__file__))!=request['implementation_sha256'] or sha256_file(recipe_path)!=request['recipe_sha256']:raise ValueError('fidelity inputs changed during execution')
        for item in staged:
            if sha256_file(run.payload_root/item['path'])!=item['sha256']:raise ValueError('staged model changed')
        verify_split_manifests(control,root/'payload-manifest.json')
    finally:
        write_json_once(run.payload_root/'summary.json',summary)
        publish_split_manifests(run,request_digest=digest,status=summary['status'],control_details={'payload_root':str(run.payload_root)},payload_details={'scope':recipe['scope']},
            control_artifacts=[run.control_root/'request.json'],payload_artifacts=[p for p in run.payload_root.rglob('*') if p.is_file()])
        verify_split_manifests(run.control_manifest,run.payload_manifest)
        print(json.dumps({'run_id':run.run_id,**summary}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('recipe',type=Path);audit(p.parse_args().recipe)
