"""Stable current-tail modes driven by a physical input-slew change."""
import math
from .sampled_state import interpolate, inputs_at, _solve


def validate(spec):
    poles=spec['poles']
    if not poles or len(poles)>64 or any(len(p)!=2 or not all(math.isfinite(v) for v in p) or not 1e-9<=p[0]<=20e-6 or not 0<=p[1]<=1e8 for p in poles):
        raise ValueError('tail poles exceed stable bounded domain')
    if not math.isfinite(spec['rate_scale']) or spec['rate_scale']<=0:
        raise ValueError('positive tail rate scale required')


def initialize(spec,values):
    validate(spec)
    return {'z':[0j]*len(spec['poles']), 'drive':0.,'speed':0., 'armed':values[spec.get('axis',0)]<.1}


def advance(spec,state,values,previous,dt):
    axis=spec.get('axis',0)
    drive=max(0.,(values[axis]-previous[axis])/dt)/spec['rate_scale'] if values[axis]>=spec['supply_min'] else 0.
    if drive>1e-7:state['speed']=drive*spec['rate_scale']
    impulse=state['drive']-drive
    state['z']=[z*complex(math.exp(-dt/tau)*math.cos(2*math.pi*freq*dt),math.exp(-dt/tau)*math.sin(2*math.pi*freq*dt))+impulse
        for z,(tau,freq) in zip(state['z'],spec['poles'])]
    state['drive']=drive
    return state['armed'] and drive==0. and values[axis]>=spec['supply_min']


def correction(spec,state):
    if 'by_slew' in spec:
        rows=[(c['rate'],sum(z.real*w[0]+z.imag*w[1] for z,w in zip(state['z'],c['coefficients']))) for c in spec['by_slew']]
        if not rows[0][0]*(1-1e-7)<=state['speed']<=rows[-1][0]*(1+1e-7):
            raise ValueError('outside calibrated tail slew domain')
        return interpolate(rows,state['speed'])
    return sum(z.real*w[0]+z.imag*w[1] for z,w in zip(state['z'],spec['coefficients']))


def fit(model,plan,waves,baselines,cases,options):
    validate(options)
    poles=options['poles']; n=2*len(poles)
    batches=[[cid] for cid in cases] if options.get('condition_by_slew') else [cases]
    fitted=[]
    for batch in batches:
        matrix=[[0.]*n for _ in range(n)]; rhs=[0.]*n; speed=None
        for cid in batch:
            case=next(c for c in plan['cases'] if c['id']==cid)
            wave=baselines[cid]; previous=inputs_at(plan,case,0.); state=initialize(options,previous)
            end=case['events'][0]['end_s']
            for i,t in enumerate(wave.times):
                dt=t-wave.times[i-1] if i else model['step_s']; values=inputs_at(plan,case,t)
                active=advance(options,state,values,previous,dt)
                if end<t<end+options['fit_tail_s'] and active and i%2==0:
                    phi=[v for z in state['z'] for v in (z.real,z.imag)]
                    target=waves[cid].sample('VPROBE:p',t)-wave.values['VPROBE:p'][i]
                    for j,v in enumerate(phi):
                        rhs[j]+=v*target
                        for k,w in enumerate(phi):matrix[j][k]+=v*w
                    speed=state['speed']
                previous=values
        if speed is None:raise ValueError('no public tail excitation')
        for i in range(n):matrix[i][i]+=1e-7
        scale=[math.sqrt(matrix[i][i]) for i in range(n)]
        coeff=[v/s for v,s in zip(_solve([[v/scale[i]/scale[j] for j,v in enumerate(row)] for i,row in enumerate(matrix)],
                                       [v/s for v,s in zip(rhs,scale)]),scale)]
        fitted.append({'rate':speed,'coefficients':[coeff[i:i+2] for i in range(0,n,2)]})
    result={**options,'training_cases':cases,'basis':'stable_slew_change_driven_complex_poles'}
    if options.get('condition_by_slew'):result['by_slew']=sorted(fitted,key=lambda r:r['rate'])
    else:result['coefficients']=fitted[0]['coefficients']
    return result


def render(spec,prefix):
    validate(spec)
    size=len(spec['poles']); axis=spec.get('axis',0)
    declarations=['real re[%d],im[%d],drive,previous_drive,speed,amplitude,co,si,tmp,tail_value; integer armed;'%(size,size)]
    lines=['if(initialize) begin armed=(x[%d]<0.1); speed=0.; previous_drive=0.; end'%axis,
        'drive=0.0;', 'if(x[%d]>=%.17g && x[%d]>prev[%d] && !initialize) drive=(x[%d]-prev[%d])/interval_s/%.17g;'%(axis,spec['supply_min'],axis,axis,axis,axis,spec['rate_scale']),
        'if(drive>1e-7) speed=drive*%.17g;'%spec['rate_scale']]
    for j,(tau,freq) in enumerate(spec['poles']):
        lines+=['if(initialize) begin re[%d]=0.0; im[%d]=0.0; end'%(j,j),
            'amplitude=$exp(-interval_s/%.17g); co=$cos(%.17g*interval_s); si=$sin(%.17g*interval_s);'%(tau,2*math.pi*freq,2*math.pi*freq),
            'tmp=re[%d]; re[%d]=amplitude*(co*re[%d]-si*im[%d])+previous_drive-drive;'%(j,j,j,j),
            'im[%d]=amplitude*(si*tmp+co*im[%d]);'%(j,j)]
    rows=spec.get('by_slew',[{'rate':0.,'coefficients':spec.get('coefficients',[])}])
    declarations+=['real value[%d];'%len(rows)]
    for j,row in enumerate(rows):
        lines+=['value[%d]='%j+'+'.join('(%.17g)*re[%d]+(%.17g)*im[%d]'%(c[0],i,c[1],i) for i,c in enumerate(row['coefficients']))+';']
    lines+=['if(armed && drive==0.0 && x[%d]>=%.17g) begin'%(axis,spec['supply_min'])]
    if 'by_slew' in spec:
        lines+=['if(speed<%.17g || speed>%.17g) $fatal(1,"TAIL_SLEW_DOMAIN");'%(rows[0]['rate']*(1-1e-7),rows[-1]['rate']*(1+1e-7)),
            'if(speed<=%.17g) tail_value=value[0];'%rows[0]['rate']]
        for j,(a,b) in enumerate(zip(rows,rows[1:])):
            lines+=['else if(speed<=%.17g) tail_value=value[%d]+(speed-%.17g)/%.17g*(value[%d]-value[%d]);'%(b['rate'],j,a['rate'],b['rate']-a['rate'],j+1,j)]
        lines+=['else tail_value=value[%d];'%(len(rows)-1)]
    else:lines+=['tail_value=value[0];']
    lines+=['supply_current_a=supply_current_a+tail_value; end','previous_drive=drive;']
    # Prefix whole identifiers only, so distinct input-tail banks cannot share state.
    import re
    names='re im drive previous_drive speed amplitude co si tmp tail_value armed value'.split()
    def rename(line):return re.sub(r'\b('+'|'.join(names)+r')\b',lambda m:prefix+m[0],line)
    return list(map(rename,declarations)),list(map(rename,lines))
