import json
import math
from pathlib import Path
import pytest
from aivworkbench.plugins.transition_response import base_goal, curve_value, schedule, response_goal
from aivworkbench.plugins.sampled_state import features


def test_sparse_parent_matches_dense_basis():
    root=Path(__file__).resolve().parents[3]/'.aivw/runs/20260908T002018.129961Z-recipe-run-658ff7e7d809/manifest.json'
    if not root.exists():pytest.skip('site public parent unavailable')
    payload=Path(json.loads(root.read_text())['details']['payload_root'])
    model=json.loads((payload/'checks/gates/model/model.json').read_text())
    for x,prev in [([2.5,1.53,.0001],[2.5,1.52875,.0001]),([1.1,2.5,.0001],[1.099,2.5,.0001]),([2.5,0.,.0001],[2.5,.001,.0001])]:
        phi=features(model,x,prev,1e-9)
        result=base_goal(model,x,prev,1e-9)
        for k,c in model['channels'].items():
            assert result[k]==pytest.approx(sum(a*b for a,b in zip(phi,c['coefficients'])),abs=1e-10)


def test_schedule_is_bounded_global_physical_time():
    model={'step_s':20e-9,'transition_response':{'fine_step_s':1e-9,'pre_s':20e-9,'tail_s':5e-6}}
    plan={'analysis':{'stop_s':.0008}}
    case={'events':[{'start_s':.0001,'end_s':.00014},{'start_s':.0005,'end_s':.00054}]}
    ts=schedule(model,plan,case)
    assert ts[0]==0 and ts[-1]==.0008 and len(ts)<200000
    assert all(b>a for a,b in zip(ts,ts[1:]))
    assert min(b-a for a,b in zip(ts,ts[1:]))==pytest.approx(1e-9)
    assert max(b-a for a,b in zip(ts,ts[1:]))==pytest.approx(20e-9)
    model['transition_response']['tail_s']=.001
    with pytest.raises(ValueError,match='budget'):schedule(model,plan,case)


def test_surface_interpolates_physical_slew_and_rejects_extrapolation():
    curve={'domain':[0.,2.],'values':[0.,1.,4.]}
    assert curve_value(curve,.5)==.5
    spec={'surfaces':[{'axis':0,'direction':1,'fixed_inputs':[[1,1.]],'channel':'current',
        'curves':[{**curve,'rate':1.},{**curve,'rate':2.,'values':[0.,2.,8.]}]}]}
    assert response_goal(spec,[1.,1.],[-.5,1.],1.,{'current':0.})['current']==1.5
    assert response_goal(spec,[1.,2.],[-.5,2.],1.,{'current':0.})['current']==0.
    with pytest.raises(ValueError,match='slew'):response_goal(spec,[1.,1.],[-2.,1.],1.,{'current':0.})


def test_response_output_inverse_reconstructs_exponential_goal():
    from aivworkbench.plugins.transition_response import fit_surface
    from aivworkbench.waveform_metrics import Waveform
    h=1e-3; tau=.1
    times=tuple(i*h for i in range(1001))
    values=tuple(1-math.exp(-t/tau) for t in times)
    wave=Waveform(times,{'v':'V'},{'v':values})
    model={'channels':{'output':{'tau_s':tau}},'transition_response':{'fine_step_s':h}}
    plan={'roles':{'supply':'s','controls':[],'output':'v'},'cases':[{'id':'ramp','voltages':{'s':{'pwl':[[0.,0.],[1.,1.]]}},'load_ohm':10000.,'load_conductance_pwl_s':None,'events':[{'start_s':0.,'end_s':1.}]}]}
    surface=fit_surface(model,plan,{'ramp':wave},cases=['ramp'],axis=0,channel='output',knots=1001)
    assert surface['curves'][0]['values'][1:]==pytest.approx([1.]*1000,abs=1e-12)


def test_multirate_rollout_keeps_independent_exponential_state():
    from aivworkbench.plugins.transition_response import simulate
    axes=[{'name':'s','knots':[0.,1.,2.],'slew_scale_per_s':1.},
          {'name':'load_conductance','knots':[0.,.0001,.001],'slew_scale_per_s':1.}]
    coeff=[1.]+[0.]*40
    model={'axes':axes,'step_s':20e-9,'identification_options':{'slew_features':'signed_polynomial'},
        'channels':{'output':{'tau_s':1e-6,'coefficients':coeff},'current':{'tau_s':0.,'coefficients':[2*v for v in coeff]}},
        'transition_response':{'fine_step_s':1e-9,'pre_s':20e-9,'tail_s':1e-6,'surfaces':[]}}
    plan={'roles':{'supply':'s','controls':[],'output':'v'},'analysis':{'stop_s':2e-6}}
    case={'voltages':{'s':{'pwl':[[0.,0.],[1e-6,1.],[2e-6,1.]]}},'load_ohm':10000.,'load_conductance_pwl_s':None,
          'events':[{'start_s':0.,'end_s':1e-6}]}
    wave=simulate(model,plan,case)
    assert wave.values['v']==pytest.approx([1.]*len(wave.times),abs=1e-12)
    assert wave.values['VPROBE:p']==pytest.approx([2.]*len(wave.times),abs=1e-12)


def test_current_tail_is_causal_stable_and_slew_conditioned():
    from aivworkbench.plugins.transition_tail import initialize,advance,correction
    spec={'axis':0,'supply_min':.9,'rate_scale':1.,'poles':[[1e-6,0.]],
          'by_slew':[{'rate':1.,'coefficients':[[1.,0.]]},{'rate':2.,'coefficients':[[2.,0.]]}]}
    state=initialize(spec,[0.])
    assert not advance(spec,state,[1.],[1.-1e-6],1e-6)
    assert advance(spec,state,[1.],[1.],1e-6)
    first=correction(spec,state)
    advance(spec,state,[1.],[1.],1e-6)
    assert correction(spec,state)==pytest.approx(first*math.exp(-1),rel=1e-7)
    assert first>0.
    initial_high=initialize(spec,[1.])
    assert not advance(spec,initial_high,[1.],[1.],1e-6)
    bad={**spec,'poles':[[-1.,0.]]}
    with pytest.raises(ValueError,match='stable'):initialize(bad,[0.])


def test_unrelated_supply_events_retain_original_integration_schedule():
    model={'step_s':20e-9,'transition_response':{'fine_step_s':1e-9,'pre_s':20e-9,'tail_s':5e-6,'fine_scope':'startup_and_control'}}
    plan={'analysis':{'stop_s':20e-6},'roles':{'supply':'s','controls':[]}}
    case={'events':[{'signal':'s','start_s':5e-6,'end_s':10e-6}],
          'voltages':{'s':{'pwl':[[0.,2.5],[5e-6,2.5],[10e-6,2.3],[20e-6,2.3]]}},'load_ohm':10000.,'load_conductance_pwl_s':None}
    times=schedule(model,plan,case)
    assert len(times)==1001
    assert all(b-a==pytest.approx(20e-9,abs=1e-18) for a,b in zip(times,times[1:]))


def test_tail_remembers_slew_and_rejects_unfitted_rate():
    from aivworkbench.plugins.transition_tail import initialize,advance,correction
    spec={'supply_min':.9,'rate_scale':1.,'poles':[[1e-6,0.]],
          'by_slew':[{'rate':1.,'coefficients':[[1.,0.]]},{'rate':2.,'coefficients':[[2.,0.]]}]}
    state=initialize(spec,[0.])
    advance(spec,state,[1.],[1.-3e-6],1e-6)
    assert advance(spec,state,[1.],[1.],1e-6)
    with pytest.raises(ValueError,match='slew'):correction(spec,state)
