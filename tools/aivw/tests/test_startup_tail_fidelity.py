import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('startup_fidelity',Path(__file__).resolve().parents[1]/'scripts/audit_startup_tail_fidelity.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_fidelity_variant_preserves_electrical_dependencies():
    deck='''simulator lang=spectre
include "../../models/gpdk045.scs" section=tt
include "../../circuit.scs"
VS_VDD (aivw_internal_supply 0) vsource type=pwl wave=[0 0 .0001 0 .000102 5 .0008 5]
VPROBE (aivw_internal_supply VDD) vsource dc=0
XUUT (VDD VOUT VSS) LDO_MASTER
RLOAD (VOUT VSS) resistor r=10000
simulatorOptions options temp=27 reltol=.001 vabstol=1e-6 iabstol=1e-12
tran tran stop=.0008 errpreset=moderate maxstep=2e-7
save VDD VOUT VSS VPROBE:p
'''
    controls={'maxstep':2e-9,'ramp_s':2e-6,'reltol':1e-6,'vabstol':1e-9,'iabstol':1e-15,'errpreset':'conservative'}
    revised=module.deck_variant(deck,controls,.0001,.000125)
    ignored=('VS_VDD ','simulatorOptions ','tran ')
    assert [s for s in deck.splitlines() if not s.startswith(ignored)]==[s for s in revised.splitlines() if not s.startswith(ignored)]
    assert 'errpreset=conservative' in revised
    with pytest.raises(ValueError,match='budget'):module.deck_variant(deck,{**controls,'maxstep':1e-12},.0001,.000125)
    with pytest.raises(ValueError,match='missing'):module.deck_variant(deck.replace('tran tran','other tran'),controls,.0001,.000125)


def test_method_override_is_explicit_and_bounded():
    deck='VS_VDD (s 0) vsource dc=0\nsimulatorOptions options temp=27\ntran tran stop=.000125\n'
    controls={'maxstep':1e-9,'ramp_s':2e-6,'reltol':1e-7,'vabstol':1e-10,'iabstol':1e-16,'errpreset':'conservative','method':'trap'}
    assert 'method=trap\n' in module.deck_variant(deck,controls,.0001,.000125)
    with pytest.raises(ValueError,match='method'):module.deck_variant(deck,{**controls,'method':'unknown'},.0001,.000125)
