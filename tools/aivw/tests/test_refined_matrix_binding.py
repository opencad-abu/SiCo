import importlib.util
import json
from copy import deepcopy
from pathlib import Path

spec=importlib.util.spec_from_file_location('refined_matrix',Path(__file__).resolve().parents[1]/'scripts/correlate_refined_public_matrix.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_projection_allows_only_numerical_version_changes():
    root=Path(__file__).resolve().parents[1]/'recipes/ldo/experiment'
    old=json.loads((root/'ldo_master_dynamic_v2.json').read_text())
    new=json.loads((root/'ldo_master_dynamic_v3.json').read_text())
    assert module.projection(old)==module.projection(new)
    for location,key,value in [('analysis','stop_s',.0007),('analysis','temperature_c',28.)]:
        changed=deepcopy(new);changed[location][key]=value
        assert module.projection(changed)!=module.projection(old)
    changed=deepcopy(new);changed['cases'][0]['load_ohm']=20000
    assert module.projection(changed)!=module.projection(old)
    changed=deepcopy(new);changed['measurement']['sample_step_s']=1e-7
    assert module.projection(changed)!=module.projection(old)
