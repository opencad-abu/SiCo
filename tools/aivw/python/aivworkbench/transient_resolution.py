"""Bounded event-local integration controls for physical transient experiments."""
import math


def validate(controls,analysis):
    if set(controls)-{'breakpoint_maxstep_s','breakpoint_window_s'}!={'method','event_maxstep_s','pre_event_s','post_event_s'}:
        raise ValueError('transient integration controls differ')
    if controls['method'] not in ('trap','gear2only'):
        raise ValueError('unsupported qualified transient method')
    for key in ('event_maxstep_s','pre_event_s','post_event_s'):
        v=controls[key]
        if isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) or v<=0:
            raise ValueError('positive finite transient resolution required')
    if ('breakpoint_maxstep_s' in controls)!=('breakpoint_window_s' in controls):raise ValueError('both breakpoint controls required')
    if 'breakpoint_maxstep_s' in controls:
        for key in ('breakpoint_maxstep_s','breakpoint_window_s'):
            value=controls[key]
            if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value):
                raise ValueError('finite numeric breakpoint resolution required')
        if not 1e-15<=controls['breakpoint_maxstep_s']<=controls['event_maxstep_s'] or not 0<controls['breakpoint_window_s']<=1e-8:raise ValueError('invalid breakpoint resolution')
    if not 1e-12<=controls['event_maxstep_s']<=analysis['maxstep_s']:
        raise ValueError('event resolution exceeds supported domain')


def schedule(controls,analysis,events):
    validate(controls,analysis)
    stop=analysis['stop_s'];coarse=analysis['maxstep_s'];fine=controls['event_maxstep_s']
    windows=[]
    for e in events:
        lo=max(0.,e['start_s']-controls['pre_event_s']);hi=min(stop,e['end_s']+controls['post_event_s'])
        if windows and lo<=windows[-1][1]:windows[-1][1]=max(hi,windows[-1][1])
        else:windows.append([lo,hi])
    if any(b[0]<a[0] for a,b in zip(windows,windows[1:])):raise ValueError('events must be ordered')
    fine_duration=sum(b-a for a,b in windows)
    count=fine_duration/fine+(stop-fine_duration)/coarse
    if count>180000:raise ValueError('scheduled transient integration exceeds sample budget')
    points=[[0.,coarse]]
    for lo,hi in windows:
        if lo==0:points[0][1]=fine
        else:points.append([lo,fine])
        if hi<stop:points.append([hi,coarse])
    if 'breakpoint_maxstep_s' in controls:
        tiny=controls['breakpoint_maxstep_s'];width=controls['breakpoint_window_s']
        boundaries=sorted(set(t for e in events for t in (e['start_s'],e['end_s'])))
        intervals=[(max(0.,t-width),min(stop,t+width)) for t in boundaries]
        ticks=sorted(set(t for t,_ in points)|{t for span in intervals for t in span})
        result=[]
        for t in ticks:
            step=next(v for x,v in reversed(points) if x<=t)
            if any(a<=t<b for a,b in intervals):step=min(step,tiny)
            if not result or result[-1][1]!=step:result.append([t,step])
        count=sum((b[0]-a[0])/a[1] for a,b in zip(result,result[1:]+[[stop,coarse]]))
        if count>180000:raise ValueError('breakpoint integration exceeds sample budget')
        return result
    return points


def render(controls,analysis,events):
    points=schedule(controls,analysis,events)
    return ' method=%s param=maxstep param_vec=[ %s ]'%(controls['method'],' '.join('%.17g %.17g'%tuple(p) for p in points))
