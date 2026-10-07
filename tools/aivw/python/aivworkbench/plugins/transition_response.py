"""Public transition-response hypothesis in input/slew coordinates.

Tables describe voltage/current response against physical input and slew, never
case identity or absolute time. The base and discharge state remain independent.
Nonuniform execution is prescribed by the stimulus scheduler, not golden data.
"""
import bisect
import math
from copy import deepcopy

from .sampled_state import inputs_at, interpolate
from .discharge_state import apply_discharge
from ..waveform_metrics import Waveform


def schedule(model, plan, case):
    spec = model['transition_response']
    from .endpoint_sampling import parameters,augment
    precision,_,_=parameters(spec)
    coarse = round(model['step_s']/precision)
    fine = round(spec['fine_step_s']/precision)
    stop = round(plan['analysis']['stop_s']/precision)
    if coarse <= 0 or fine <= 0 or coarse % fine:
        raise ValueError('transition steps must divide on simulator precision')
    ticks = set(range(0, stop+1, coarse)) | {stop}
    for event in case['events']:
        if spec.get('fine_scope')=='startup_and_control':
            signal=event['signal']
            if signal not in plan['roles']['controls'] and not (signal==plan['roles']['supply'] and inputs_at(plan,case,0.)[0]<.1):
                continue
        lo = max(0, math.floor((event['start_s']-spec['pre_s'])/spec['fine_step_s']))
        hi = min(stop//fine, math.ceil((event['end_s']+spec['tail_s'])/spec['fine_step_s']))
        ticks.update(i*fine for i in range(lo, hi+1))
    endpoint_events=case['events']
    if spec.get('endpoint_scope')=='startup':
        endpoint_events=[e for e in endpoint_events if e['signal']==plan['roles']['supply'] and inputs_at(plan,case,0.)[0]<.1]
    augment(ticks,spec,endpoint_events,stop)
    if len(ticks) > 200000:
        raise ValueError('transition schedule exceeds 200000 sample budget')
    result=[t*precision for t in sorted(ticks)]
    result[-1]=plan['analysis']['stop_s']
    return tuple(result)


def base_goal(model, values, previous, dt):
    """Sparse evaluation of the unchanged signed-polynomial parent basis."""
    if model['identification_options']['slew_features'] != 'signed_polynomial':
        raise ValueError('signed polynomial base required')
    active = [(0, 1.)]
    supports = []
    offset = 1
    for value, axis in zip(values, model['axes']):
        knots = axis['knots']
        if not knots[0]-1e-12 <= value <= knots[-1]+1e-12:
            raise ValueError('outside identified input domain')
        value = max(knots[0], min(knots[-1], value))
        j = min(len(knots)-2, max(0, bisect.bisect_right(knots, value)-1))
        f = (value-knots[j])/(knots[j+1]-knots[j])
        support = [(j,1-f),(j+1,f)]
        supports.append(support)
        active.extend((offset+k,w) for k,w in support if w and k<len(knots)-1)
        offset += len(knots)-1
    for value, old, axis, support in zip(values, previous, model['axes'], supports):
        slew = (value-old)/dt/axis['slew_scale_per_s']
        for direction in (1.,-1.):
            for power in (.5,1.,2.):
                amp = max(0.,direction*slew)**power
                if amp:
                    active.extend((offset+k,amp*w) for k,w in support if w)
                offset += len(axis['knots'])
    return {k: sum(v*c['coefficients'][j] for j,v in active) for k,c in model['channels'].items()}


def curve_value(curve, x):
    lo, hi = curve['domain']
    ys = curve['values']
    if 'coordinates' in curve:
        xs=curve['coordinates']; x=max(xs[0],min(xs[-1],x))
        j=min(len(xs)-2,max(0,bisect.bisect_right(xs,x)-1))
        f=(x-xs[j])/(xs[j+1]-xs[j])
        return ys[j]*(1-f)+ys[j+1]*f
    u = max(0., min(len(ys)-1., (x-lo)/(hi-lo)*(len(ys)-1)))
    j = min(len(ys)-2,int(u)); f=u-j
    return ys[j]*(1-f)+ys[j+1]*f


def response_goal(spec, values, previous, dt, goals):
    goals = dict(goals)
    for family in spec['surfaces']:
        axis = family['axis']; rate=(values[axis]-previous[axis])/dt
        if 'maximum_input' in family and values[axis]>family['maximum_input']+family.get('input_roundoff_v',0.):
            continue
        if rate*family['direction'] <= 1e-3:
            continue
        if any(abs(values[i]-v)>1e-9 for i,v in family['fixed_inputs']):
            continue
        speed=abs(rate)
        curves=family['curves']
        if not curves[0]['rate']*(1-1e-7) <= speed <= curves[-1]['rate']*(1+1e-7):
            raise ValueError('outside transition slew calibration domain')
        points=[(c['rate'],curve_value(c,values[axis])) for c in curves]
        goals[family['channel']] = interpolate(points,speed)
    return goals


def simulate(model, plan, case):
    ts=schedule(model,plan,case)
    previous=inputs_at(plan,case,0.)
    state=base_goal(model,previous,previous,model['step_s'])
    outputs=[]; currents=[]
    spec=model['transition_response']
    from .transition_tail import initialize,advance,correction
    tail_specs=[spec[k] for k in ('tail','enable_tail') if k in spec]
    tails=[initialize(t,previous) for t in tail_specs]
    for i,t in enumerate(ts):
        dt=ts[i]-ts[i-1] if i else model['step_s']
        values=inputs_at(plan,case,t)
        goals=base_goal(model,values,previous,dt)
        goals=response_goal(spec,values,previous,dt,goals)
        for name,c in model['channels'].items():
            a=math.exp(-dt/c['tau_s']) if c['tau_s'] else 0.
            state[name]=goals[name] if not i else a*state[name]+(1-a)*goals[name]
        v=state['output']; current=state['current']
        if 'discharge' in model:
            local={**model,'step_s':dt}
            v=apply_discharge(local,v,outputs[-1] if outputs else v,values,previous,not i)
        for tail,tail_state in zip(tail_specs,tails):
            if advance(tail,tail_state,values,previous,dt):
                current+=correction(tail,tail_state)
        outputs.append(v); currents.append(current); previous=values
    return Waveform(ts,{plan['roles']['output']:'V','VPROBE:p':'A'},
                    {plan['roles']['output']:tuple(outputs),'VPROBE:p':tuple(currents)})


def fit_surface(model, plan, waves, *, cases, axis, channel, knots):
    if not 3<=knots<=4097:
        raise ValueError('bounded transition knot count required')
    curves=[]; fixed=None; direction=None
    signal=plan['roles']['output'] if channel=='output' else 'VPROBE:p'
    tau=model['channels'][channel]['tau_s']
    for cid in cases:
        case=next(c for c in plan['cases'] if c['id']==cid)
        event=case['events'][0]
        start,end=event['start_s'],event['end_s']
        a,b=inputs_at(plan,case,start),inputs_at(plan,case,end)
        this_fixed=[[i,v] for i,v in enumerate(a) if i!=axis]
        this_direction=1 if b[axis]>a[axis] else -1
        if fixed is not None and (this_fixed!=fixed or this_direction!=direction):
            raise ValueError('transition family operating points differ')
        fixed=this_fixed; direction=this_direction
        if any(a[i]!=b[i] for i in range(len(a)) if i!=axis):
            raise ValueError('single-axis training required')
        wave=waves[cid]; lo,hi=sorted([a[axis],b[axis]])
        vals=[]; h=model['transition_response']['fine_step_s']
        for j in range(knots):
            x=lo+(hi-lo)*j/(knots-1)
            t=start+(x-a[axis])/(b[axis]-a[axis])*(end-start)
            # The output goal is the exact exponential-step inverse at the
            # fine step, using public calibration only. Runtime is closed loop.
            now=wave.sample(signal,t)
            if tau:
                decay=math.exp(-h/tau)
                value=(now-decay*wave.sample(signal,max(start,t-h)))/(1-decay)
            else:
                value=now
            vals.append(value)
        curves.append({'rate':abs(b[axis]-a[axis])/(end-start),'domain':[lo,hi],'values':vals})
    return {'axis':axis,'direction':direction,'channel':channel,'fixed_inputs':fixed,
            'curves':sorted(curves,key=lambda c:c['rate']), 'training_cases':cases}


def render_model(model):
    from .sampled_state_sv import render_model as base_render
    plain=deepcopy(model); plain.pop('transition_response')
    source=base_render(plain)
    spec=model['transition_response']; dt=model['step_s']
    source=source.replace('task automatic advance(input integer initialize); begin',
                          'task automatic advance(input integer initialize, input real interval_s); begin')
    for i,axis in enumerate(model['axes']):
        source=source.replace('(x[%d]-prev[%d])/%.17g;'%(i,i,dt*axis['slew_scale_per_s']),
                              '(x[%d]-prev[%d])/(interval_s*%.17g);'%(i,i,axis['slew_scale_per_s']))
    for name,goal in [('output','goal_v'),('current','goal_i')]:
        state=model['roles']['output'] if name=='output' else 'supply_current_a'
        if name=='output' and 'discharge' in model: state='discharge_base'
        tau=model['channels'][name]['tau_s']; a=math.exp(-dt/tau) if tau else 0.
        old='%s=initialize ? %s : %.17g*%s + %.17g*%s;'%(state,goal,a,state,1-a,goal)
        new='%s=initialize ? %s : $exp(-interval_s/%.17g)*%s+(1.0-$exp(-interval_s/%.17g))*%s;'%(state,goal,tau,state,tau,goal) if tau else old
        # Put coordinate response before the independent parent state advance.
        edits=[]
        for n,f in enumerate(spec['surfaces']):
            if f['channel']!=name:continue
            axis=f['axis']; curves=f['curves']
            cond=' && '.join('x[%d]>=%.17g && x[%d]<=%.17g'%(i,v-1e-9,i,v+1e-9) for i,v in f['fixed_inputs'])
            if 'maximum_input' in f:cond+=' && x[%d]<=%.17g'%(axis,f['maximum_input']+f.get('input_roundoff_v',0.))
            edits+=['tr_rate=(x[%d]-prev[%d])/interval_s;'%(axis,axis),
                    'if(!initialize && tr_rate*%d>0.001 && %s) begin'%(f['direction'],cond),
                    'tr_rate=tr_rate*%d;'%f['direction'],
                    'if(tr_rate<%.17g || tr_rate>%.17g) $fatal(1,"TRANSITION_SLEW_DOMAIN");'%(curves[0]['rate']*(1-1e-7),curves[-1]['rate']*(1+1e-7)),
                    'if(tr_rate<=%.17g) %s=tr_curve_%d_0(x[%d]);'%(curves[0]['rate'],goal,n,axis)]
            for j,(a0,b0) in enumerate(zip(curves,curves[1:])):
                edits+=['else if(tr_rate<=%.17g) %s=tr_curve_%d_%d(x[%d])+(tr_rate-%.17g)/%.17g*(tr_curve_%d_%d(x[%d])-tr_curve_%d_%d(x[%d]));'%(b0['rate'],goal,n,j,axis,a0['rate'],b0['rate']-a0['rate'],n,j+1,axis,n,j,axis)]
            edits+=['else %s=tr_curve_%d_%d(x[%d]);'%(goal,n,len(curves)-1,axis),'end']
        source=source.replace(old,'\n'.join(edits+[new]))
    if 'discharge' in model:
        source=source.replace('$exp(-%.17g*$exp(discharge_lograte))'%dt,'$exp(-interval_s*$exp(discharge_lograte))')
    declarations=['real tr_rate;']
    for n,f in enumerate(spec['surfaces']):
        for j,c in enumerate(f['curves']):
            name='tr_curve_%d_%d'%(n,j); arr='tr_values_%d_%d'%(n,j)
            if 'coordinates' in c:
                xs=arr+'_x';size=len(c['values'])
                declarations+=['real %s[0:%d],%s[0:%d];'%(arr,size-1,xs,size-1),'initial begin']
                declarations+=['%s[%d]=%.17g; %s[%d]=%.17g;'%(arr,k,v,xs,k,x) for k,(x,v) in enumerate(zip(c['coordinates'],c['values']))]
                declarations+=['end','function automatic real %s(input real v); real f; integer lo,hi,mid; begin'%name,
                    'lo=0; hi=%d;'% (size-1),
                    'while(hi-lo>1) begin mid=(hi+lo)/2; if(v<%s[mid]) hi=mid; else lo=mid; end'%xs,
                    'f=(v-%s[lo])/(%s[hi]-%s[lo]); if(f<0.0) f=0.0; if(f>1.0) f=1.0;'%(xs,xs,xs),
                    '%s=%s[lo]*(1.0-f)+%s[hi]*f; end endfunction'%(name,arr,arr)]
                continue
            declarations+=['real %s[0:%d];'%(arr,len(c['values'])-1),'initial begin']
            declarations+=['%s[%d]=%.17g;'%(arr,k,v) for k,v in enumerate(c['values'])]
            declarations+=['end','function automatic real %s(input real v); real u,f; integer k; begin'%name,
                'u=(v-%.17g)/%.17g*%d;'%(c['domain'][0],c['domain'][1]-c['domain'][0],len(c['values'])-1),
                'if(u<0.0) u=0.0; if(u>%d) u=%d;'%(len(c['values'])-1,len(c['values'])-1),
                'k=$rtoi(u); if(k>=%d) k=%d; f=u-k;'%(len(c['values'])-1,len(c['values'])-2),
                '%s=%s[k]*(1.0-f)+%s[k+1]*f; end endfunction'%(name,arr,arr)]
    from .transition_tail import render as render_tail
    for key in ('tail','enable_tail'):
        if key in spec:
            decl,body=render_tail(spec[key],key+'_')
            declarations+=decl
            source=source.replace('for(j=0;j<','\n'.join(body)+'\nfor(j=0;j<')
    return source.replace('function automatic real hat','\n'.join(declarations)+'\nfunction automatic real hat')


def render_testbench(model, plan, case):
    from .sampled_state_sv import _function
    from ..electrical_experiment import identifier
    from .endpoint_sampling import parameters
    precision,_,_=parameters(model['transition_response'])
    ts=schedule(model,plan,case); ticks=[round(t/precision) for t in ts]
    # Compress the prescribed schedule into constant-step segments.
    segments=[]; start=1
    while start<len(ticks):
        step=ticks[start]-ticks[start-1]; end=start+1
        while end<len(ticks) and ticks[end]-ticks[end-1]==step:end+=1
        segments.append((ticks[start-1],step,end-start)); start=end
    out=plan['roles']['output']; stop=plan['analysis']['stop_s']
    lines=['module trajectory_tb; timeunit 1ns; timeprecision %s;'%('1fs' if precision==1e-15 else '1ps'), 'integer fd,k; real t;',
        *['real %s;'%identifier(p) for p in model['ports']],
        '%s dut(%s);'%(model['module'],', '.join('.%s(%s)'%(p,p) for p in model['ports']))]
    for pin,src in case['voltages'].items():
        lines+=_function('drive_'+pin,src.get('pwl',[[0.,src.get('dc_v')],[stop,src.get('dc_v')]]))
    lines+=_function('drive_load',case['load_conductance_pwl_s'] or [[0.,1/case['load_ohm']],[stop,1/case['load_ohm']]])
    lines+=['task automatic sample(input integer initialize, input real dt); begin',
        *['%s=drive_%s(t);'%(p,p) for p in case['voltages']],
        'dut.load_conductance=drive_load(t); #0; dut.advance(initialize,dt); #0;',
        '$fwrite(fd,"%.17g,%.17g,%.17g\\n",t,'+out+',dut.supply_current_a);','end endtask',
        'initial begin fd=$fopen("waveforms.csv","w"); if(!fd) $fatal(1,"CSV_OPEN_FAILED");',
        '$fwrite(fd,"time[s],%s[V],VPROBE:p[A]\\n");'%out,
        't=0.; sample(1,%.17g);'%model['step_s']]
    for initial,step,count in segments:
        if precision==1e-12:
            lines+=['for(k=1;k<=%d;k=k+1) begin #(%0.17g); t=(%d+k*%d)*1e-12; sample(0,%.17g); end'%(count,step*.001,initial,step,step*1e-12)]
        else:
            lines+=['for(k=1;k<=%d;k=k+1) begin #(%0.17g); t=(%.1f+k*%.1f)*%.17g; sample(0,%.17g); end'%(count,step*precision/1e-9,float(initial),float(step),precision,step*precision)]
    lines+=['$fclose(fd); $display("AIVW_TRAJECTORY_COMPLETE"); $finish; end endmodule']
    return '\n'.join(lines)+'\n'


def fit_tail(model,plan,waves,baselines,cases,options):
    from .transition_tail import fit
    return fit(model,plan,waves,baselines,cases,options)
