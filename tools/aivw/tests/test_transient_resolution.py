import json
from pathlib import Path
import pytest
from aivworkbench.electrical_experiment import render_deck,validate_experiment
from aivworkbench.transient_resolution import schedule


def test_complete_reference_preserves_stimulus_and_bounds_local_steps():
    root=Path(__file__).resolve().parents[1]/'recipes/ldo/experiment'
    for target in ('master','aon'):
        old=json.loads((root/f'ldo_{target}_dynamic_v2.json').read_text())
        for version in (('v3','v4') if target=='master' else ('v3','v4','v5','v6','v7','v8')):
            plan=validate_experiment(json.loads((root/f'ldo_{target}_dynamic_{version}.json').read_text()))
            assert plan['cases']==old['cases']
            assert plan['analysis']['stop_s']==.0008
            for c in plan['cases']:
                a=plan['analysis'];points=schedule(a['transient_resolution'],a,c['events'])
                assert points[0][0]==0
                assert all(y[0]>x[0] for x,y in zip(points,points[1:]))
                text=render_deck(plan,c['id']);original=render_deck(old,c['id'])
                assert 'param=maxstep param_vec=[' in text
                assert [l for l in text.splitlines() if not l.startswith(('tran tran','simulatorOptions'))]==[l for l in original.splitlines() if not l.startswith(('tran tran','simulatorOptions'))]


def test_resolution_budget_and_method_reject_invalid_controls():
    analysis={'maxstep_s':2e-7,'stop_s':.0008}
    controls={'method':'trap','event_maxstep_s':1e-9,'pre_event_s':2e-8,'post_event_s':10e-6}
    with pytest.raises(ValueError,match='budget'):schedule(controls,analysis,[{'start_s':0.,'end_s':.0007}])
    with pytest.raises(ValueError,match='method'):schedule({**controls,'method':'bad'},analysis,[])


def test_breakpoint_windows_overlap_and_clip_without_duplicate_ticks():
    analysis={'maxstep_s':2e-7,'stop_s':1e-6}
    controls={'method':'trap','event_maxstep_s':1e-9,'pre_event_s':2e-8,
              'post_event_s':1e-8,'breakpoint_maxstep_s':1e-13,'breakpoint_window_s':1e-10}
    events=[{'start_s':0.,'end_s':1e-10}, {'start_s':analysis['stop_s']-1e-10,'end_s':analysis['stop_s']}]
    points=schedule(controls,analysis,events)
    assert points[0]==[0.,1e-13]
    assert all(a[0]<b[0] for a,b in zip(points,points[1:]))
    assert all(0<=t<=analysis['stop_s'] for t,_ in points)
    def step(t):return next(v for x,v in reversed(points) if x<=t)
    assert step(1.5e-10)==1e-13
    assert step(3e-10)==1e-9
    assert step(5e-7)==2e-7
    assert step(analysis['stop_s']-5e-11)==1e-13
    with pytest.raises(ValueError,match='budget'):
        schedule({**controls,'breakpoint_maxstep_s':1e-15,'breakpoint_window_s':1e-8},analysis,events)
    with pytest.raises(ValueError,match='both breakpoint'):
        schedule({k:v for k,v in controls.items() if k!='breakpoint_window_s'},analysis,events)


@pytest.mark.parametrize('field',['breakpoint_maxstep_s','breakpoint_window_s'])
@pytest.mark.parametrize('value',[True,False,None,'1e-13',float('nan'),float('inf'),-1.,0.])
def test_breakpoint_controls_reject_non_numeric_or_nonpositive(field,value):
    analysis={'maxstep_s':2e-7,'stop_s':1e-6}
    controls={'method':'trap','event_maxstep_s':1e-9,'pre_event_s':2e-8,
              'post_event_s':1e-8,'breakpoint_maxstep_s':1e-13,'breakpoint_window_s':1e-10}
    with pytest.raises(ValueError,match='breakpoint'):
        schedule({**controls,field:value},analysis,[])
