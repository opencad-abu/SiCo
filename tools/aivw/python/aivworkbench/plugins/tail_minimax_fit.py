"""Bounded reweighted least-squares tail fitting, independent of acceptance policy."""
import math
from .sampled_state import _solve


def solve(rows, *, passes=4):
    """Rows are (features, current residual A, reference current A).

    Relative weighting balances current magnitude; Lawson-style passes target
    localized errors without adding basis functions or using verdict thresholds.
    """
    if not rows or not 1<=passes<=8 or len(rows)>20000:
        raise ValueError('bounded tail fit rows/passes required')
    n=len(rows[0][0])
    if not 1<=n<=128 or any(len(p)!=n or not all(math.isfinite(x) for x in [*p,y,scale]) for p,y,scale in rows):
        raise ValueError('invalid bounded tail basis')
    scales=[max(abs(current),1e-4) for _,_,current in rows]
    weights=[1.]*len(rows);answer=None;history=[]
    for iteration in range(passes):
        matrix=[[0.]*n for _ in range(n)];rhs=[0.]*n
        for (phi,target,_),scale,weight in zip(rows,scales,weights):
            w=weight*(1e-3/scale)**2
            for j,v in enumerate(phi):
                rhs[j]+=w*v*target
                for k,x in enumerate(phi):matrix[j][k]+=w*v*x
        for i in range(n):matrix[i][i]+=1e-7
        diag=[math.sqrt(matrix[i][i]) for i in range(n)]
        result=_solve([[x/diag[i]/diag[j] for j,x in enumerate(row)] for i,row in enumerate(matrix)], [v/s for v,s in zip(rhs,diag)])
        coeff=[v/s for v,s in zip(result,diag)]
        errors=[abs(sum(x*y for x,y in zip(phi,coeff))-target)/scale for (phi,target,_),scale in zip(rows,scales)]
        worst=max(errors);history.append(worst)
        if answer is None or worst<answer[0]:answer=(worst,coeff)
        if worst<1e-12:break
        weights=[min(100.,max(.01,w*max(.1,e/worst))) for w,e in zip(weights,errors)]
        maximum=max(weights);weights=[w/maximum for w in weights]
    return answer[1],{'passes':len(history),'maximum_relative_residual_by_pass':history,'best_maximum_relative_residual':answer[0],
                     'fitting_current_scale_floor_a':1e-4,'acceptance_policy_used':False}


def fit_row(model,plan,case,reference,baseline,options,passes):
    from .sampled_state import inputs_at
    from .transition_tail import initialize,advance
    previous=inputs_at(plan,case,0.);state=initialize(options,previous);rows=[]
    end=case['events'][0]['end_s'];speed=None
    for i,t in enumerate(baseline.times):
        dt=t-baseline.times[i-1] if i else model['step_s'];values=inputs_at(plan,case,t)
        active=advance(options,state,values,previous,dt)
        if end<t<end+options['fit_tail_s'] and active and i%2==0:
            phi=[v for z in state['z'] for v in (z.real,z.imag)]
            current=reference.sample('VPROBE:p',t)
            rows.append((phi,current-baseline.values['VPROBE:p'][i],current));speed=state['speed']
        previous=values
    coefficients,report=solve(rows,passes=passes)
    return {'rate':speed,'coefficients':[coefficients[i:i+2] for i in range(0,len(coefficients),2)]},report
