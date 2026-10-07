"""Bounded input-event endpoint sampling on an explicit simulator tick grid."""
import math


def parameters(spec):
    precision=spec.get('timeprecision_s',1e-12)
    if precision not in (1e-12,1e-15):
        raise ValueError('unsupported simulator time precision')
    if 'endpoint_step_s' not in spec and 'endpoint_window_s' not in spec:
        return precision,None,None
    step=spec.get('endpoint_step_s'); window=spec.get('endpoint_window_s')
    if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in (step,window)):
        raise ValueError('finite paired endpoint sampling controls required')
    ticks=round(step/precision)
    if ticks<1 or abs(ticks*precision-step)>precision*1e-6 or not step<=window<=1e-8:
        raise ValueError('endpoint sampling exceeds bounded precision domain')
    if step>spec['fine_step_s']:
        raise ValueError('endpoint step must refine the event grid')
    return precision,step,window


def augment(ticks,spec,events,stop):
    precision,step,window=parameters(spec)
    if step is None:return
    stride=round(step/precision);width=round(window/precision)
    for event in events:
        for time in (event['start_s'],event['end_s']):
            center=round(time/precision)
            lo=max(0,center-width);hi=min(stop,center+width)
            if (hi-lo)//stride+len(ticks)>200000:
                raise ValueError('endpoint schedule exceeds 200000 sample budget')
            ticks.update(range(lo,hi+1,stride));ticks.add(center)


def fit_current_endpoints(surface,plan,waves,step,window):
    """Add source-sampled input-coordinate knots near public ramp endpoints."""
    if surface['channel']!='current' or not 0<step<=window<=1e-8:
        raise ValueError('bounded current endpoint fit required')
    from .sampled_state import inputs_at
    axis=surface['axis'];out=[]
    for cid in surface['training_cases']:
        case=next(c for c in plan['cases'] if c['id']==cid)
        event=case['events'][0];start,end=event['start_s'],event['end_s']
        a=inputs_at(plan,case,start)[axis];b=inputs_at(plan,case,end)[axis]
        rate=abs(b-a)/(end-start)
        curve=min(surface['curves'],key=lambda c:abs(c['rate']-rate))
        times={start+(end-start)*j/(len(curve['values'])-1) for j in range(len(curve['values']))}
        for j in range(round(window/step)+1):
            times.update((min(end,start+j*step),max(start,end-j*step)))
        points=sorted({inputs_at(plan,case,t)[axis]:waves[cid].sample('VPROBE:p',t) for t in times}.items())
        if len(points)>8193:raise ValueError('endpoint coordinate curve exceeds 8193 knots')
        out.append({**curve,'coordinates':[x for x,_ in points],'values':[y for _,y in points]})
    return {**surface,'curves':sorted(out,key=lambda c:c['rate'])}
