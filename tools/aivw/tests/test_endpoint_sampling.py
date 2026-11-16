import pytest
from aivworkbench.plugins.endpoint_sampling import parameters,augment,fit_current_endpoints
from aivworkbench.plugins.transition_response import curve_value,schedule,response_goal
from aivworkbench.waveform_metrics import Waveform


def test_endpoint_grid_keeps_native_physical_edges_and_is_bounded():
    spec={'fine_step_s':1e-9,'timeprecision_s':1e-15,'endpoint_step_s':1e-13,'endpoint_window_s':1e-10}
    ticks={0,800000000000}
    events=[{'start_s':1e-4,'end_s':1.02e-4}]
    augment(ticks,spec,events,800000000000)
    center=round(1e-4/1e-15)
    assert center in ticks and center+100 in ticks and center-100 in ticks
    assert len(ticks)<5000
    with pytest.raises(ValueError,match='budget'):
        augment(set(range(199999)),spec,events,800000000000)
    with pytest.raises(ValueError,match='precision'):
        parameters({**spec,'endpoint_step_s':1e-16})


def test_nonuniform_current_curve_preserves_fast_endpoint_and_interior():
    plan={'roles':{'supply':'s','controls':[]},'cases':[{'id':'r','events':[{'start_s':0.,'end_s':1e-6}],
        'voltages':{'s':{'pwl':[[0.,0.],[1e-6,1.]]}},'load_ohm':10000.,'load_conductance_pwl_s':None}]}
    wave=Waveform((0.,1e-13,1e-10,1e-6),{'VPROBE:p':'A'},{'VPROBE:p':(0.,1e-5,1.1e-5,1e-3)})
    surface={'axis':0,'channel':'current','training_cases':['r'],'curves':[{'rate':1e6,'domain':[0.,1.],'values':[0.,.0005,.001]}]}
    fit=fit_current_endpoints(surface,plan,{'r':wave},1e-13,1e-10)['curves'][0]
    assert curve_value(fit,1e-7)==pytest.approx(1e-5)
    assert curve_value(fit,5e-8)==pytest.approx(5e-6)
    assert curve_value(fit,.5)==pytest.approx(wave.sample('VPROBE:p',.5e-6))
    assert all(a<b for a,b in zip(fit['coordinates'],fit['coordinates'][1:]))


def test_startup_endpoint_scope_does_not_refine_high_supply_events():
    spec={'fine_step_s':1e-9,'timeprecision_s':1e-15,'endpoint_step_s':1e-13,
          'endpoint_window_s':1e-10,'endpoint_scope':'startup','fine_scope':'startup_and_control',
          'pre_s':20e-9,'tail_s':1e-6}
    model={'step_s':20e-9,'transition_response':spec}
    plan={'roles':{'supply':'s','controls':[]},'analysis':{'stop_s':20e-6}}
    case={'events':[{'signal':'s','start_s':5e-6,'end_s':10e-6}],
          'voltages':{'s':{'pwl':[[0.,2.5],[5e-6,2.5],[10e-6,2.3],[20e-6,2.3]]}},
          'load_ohm':10000.,'load_conductance_pwl_s':None}
    assert len(schedule(model,plan,case))==1001
    case['voltages']['s']['pwl']=[[0.,0.],[5e-6,0.],[10e-6,2.5],[20e-6,2.5]]
    axis=schedule(model,plan,case)
    assert min(b-a for a,b in zip(axis,axis[1:]))==pytest.approx(1e-13,abs=1e-20)


def test_coordinate_cutoff_has_explicit_roundoff_guard_not_physical_extension():
    family={'axis':0,'direction':1,'fixed_inputs':[[1,1.]],'channel':'current',
            'maximum_input':2.18875,'input_roundoff_v':1e-12,
            'curves':[{'rate':1.,'domain':[0.,3.],'values':[5.,5.]}]}
    spec={'surfaces':[family]}
    boundary=family['maximum_input']
    for value in (boundary-4e-16,boundary,boundary+4e-16):
        assert response_goal(spec,[value,1.],[value-1.,1.],1.,{'current':0.})['current']==5.
    value=boundary+1e-9
    assert response_goal(spec,[value,1.],[value-1.,1.],1.,{'current':0.})['current']==0.
