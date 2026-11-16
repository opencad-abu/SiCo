"""Result coverage and acceptance independent of circuit/example names."""
import copy
import json
import pytest
from cadai.result_contract import ResultError, normalize_capture, evaluate, digest
from cadai.result_tools import ResultStore, call_result_tool, capture
from cadai.mcp import McpServer, SERVER_INSTRUCTIONS
from test_circuit_tools import call


def fixture_data():
    records = []
    for point, corner, value in [(7, 'slow_hot', 1.0), (42, 'fast_cold', 1.2)]:
        records += [['coordinate', corner, point, [['supply', 'corner', 1.8], ['model', 'corner', [['/p/model.scs', corner]]]]],
                    ['test', 'arbitrary_characterization', corner, point, 'done', 'done'],
                    ['output', 'arbitrary_characterization', corner, point, 'voltage_at_t', 'scalar', value]]
    records += [['definition', 'arbitrary_characterization', 'voltage_at_t', 'point', '(value (VT "/out") 1e-9)']]
    meta = dict(source='maeReadResDB', scope='point_expressions', target=['a_work_lib', 'a_cell', 'custom_ade'],
                library_path='/work/library', history='CustomHistory.9', test_points=2, outputs=2)
    snapshot = normalize_capture(meta, records)
    contract = dict(source='user task voltage acceptance',
                    expected_test_points=[dict(test='arbitrary_characterization', corner=c, point=p)
                                          for p,c in [(7,'slow_hot'),(42,'fast_cold')]],
                    specifications=[dict(test='arbitrary_characterization', output='voltage_at_t', unit='V',lower=0.9,upper=1.1)])
    return meta, records, snapshot, contract


def test_exact_coordinates_worst_failure_and_unit_provenance():
    _,_,snapshot,contract=fixture_data()
    report=evaluate(snapshot,contract)
    assert report['qualification']=='fail' and report['spec_qualified'] is False
    assert report['coverage_complete']
    assert report['counts']=={'pass':1,'fail':1,'missing':0}
    assert report['summaries'][0]['worst']['point']==42
    assert report['summaries'][0]['worst']['margin']==pytest.approx(-.1)
    assert all(r['unit_source']=='explicit_contract' for r in report['rows'])
    assert all(r['unit'] is None for r in snapshot['outputs'])


def test_order_does_not_change_captured_identity():
    meta,records,snapshot,_=fixture_data()
    assert digest(normalize_capture(meta,list(reversed(records))))==digest(snapshot)


@pytest.mark.parametrize('params', [None, []])
def test_nominal_points_without_design_variables(params):
    meta, records, _, contract = fixture_data()
    for row in records:
        if row[0] == 'coordinate':
            row[3] = params
    result = normalize_capture(meta, records)
    assert all(row['parameters'] == [] for row in result['coordinates'])
    contract['specifications'][0]['upper'] = 2
    assert evaluate(result, contract)['qualification'] == 'pass'


@pytest.mark.parametrize('status',['missing','error','non_scalar'])
def test_unavailable_values_do_not_qualify(status):
    meta,records,_,contract=fixture_data()
    records[5][-2:]=[status,None]
    report=evaluate(normalize_capture(meta,records),contract)
    assert report['qualification']=='incomplete' and report['spec_qualified'] is None
    assert report['counts']['missing']==1 and not report['coverage_complete']


def test_missing_whole_point_and_unexpected_point_never_pass():
    _,_,snapshot,contract=fixture_data()
    contract['specifications'][0]['upper']=2
    for scenario in ('missing','extra'):
        value=copy.deepcopy(snapshot)
        if scenario=='missing': value['tests']=value['tests'][:1]
        else:
            extra={**value['tests'][0],'point':100};value['tests'].append(extra)
        report=evaluate(value,contract)
        assert report['qualification']=='incomplete' and report['spec_qualified'] is None
        assert not report['coverage_complete']


def test_failed_test_with_numeric_output_cannot_pass():
    _,_,snapshot,contract=fixture_data()
    snapshot['tests'][0]['status']='failed'
    contract['specifications'][0]['upper']=2
    report=evaluate(snapshot,contract)
    assert report['counts']['missing']==1 and report['qualification']=='incomplete'


def test_known_failure_and_missing_are_both_retained():
    _,_,snapshot,contract=fixture_data()
    contract['expected_test_points'].append(dict(test='arbitrary_characterization',corner='missing_corner',point=1))
    report=evaluate(snapshot,contract)
    assert report['qualification']=='fail' and not report['coverage_complete']
    assert report['counts']['fail']==1 and report['counts']['missing']==1


@pytest.mark.parametrize('inclusive',[True,False])
def test_boundary_equality_and_zero_are_explicit(inclusive):
    _,_,snapshot,contract=fixture_data()
    for row in snapshot['outputs']:row['value']=0
    contract['specifications'][0].update(lower=0,upper=1,lower_inclusive=inclusive)
    report=evaluate(snapshot,contract)
    assert report['qualification']==('pass' if inclusive else 'fail')
    assert report['summaries'][0]['worst']['margin']==0


@pytest.mark.parametrize('change',[
    lambda c:c.pop('source'), lambda c:c.update(expected_test_points=[]),
    lambda c:c['expected_test_points'].append(c['expected_test_points'][0]),
    lambda c:c['specifications'][0].pop('unit'), lambda c:c['specifications'][0].update(upper=float('nan')),
    lambda c:c['specifications'][0].update(lower=True),lambda c:c['specifications'][0].update(lower=2),
    lambda c:c['specifications'][0].update(lower=10**400),
    lambda c:c['specifications'][0].update(lower=1.1,lower_inclusive=False),
    lambda c:c['specifications'][0].update(lower_inclusive='false'),
    lambda c:c['specifications'][0].update(test='not_expected'),
    lambda c:c['specifications'].append(c['specifications'][0]),
    lambda c:c.update(specifications=[]),lambda c:c.update(assume_all_points=True),
])
def test_ambiguous_contract_rejected(change):
    _,_,snapshot,contract=fixture_data();change(contract)
    with pytest.raises(ResultError):evaluate(snapshot,contract)


@pytest.mark.parametrize('mutate',[
    lambda m,r:r.append(r[-2]),lambda m,r:r[2].__setitem__(-1,float('inf')),
    lambda m,r:r[2].__setitem__(-1,True),lambda m,r:r[2].__setitem__(-2,'unknown'),
    lambda m,r:m.update(outputs=99),lambda m,r:r.pop(),
    lambda m,r:r[0][-1].append(r[0][-1][0]),lambda m,r:r[2].__setitem__(3,999),
    lambda m,r:r[2].__setitem__(-2,'error')])
def test_malformed_or_truncated_native_capture_rejected(mutate):
    meta,records,_,_=fixture_data();mutate(meta,records)
    with pytest.raises(ResultError):normalize_capture(meta,records)


def test_snapshot_artifact_integrity_and_restart_query(tmp_path):
    _,_,snapshot,contract=fixture_data();store=ResultStore(tmp_path)
    ref,artifact=store.put('results',snapshot)
    assert store.put('results',snapshot)[0]==ref
    response=call_result_tool('query_maestro_results',dict(result_ref=ref,limit=1),workspace=tmp_path,client=None)
    assert len(response['items'])==1 and response['next_offset']==1
    report=call_result_tool('evaluate_measurement_specs',dict(result_ref=ref,contract=contract),workspace=tmp_path,client=None)
    assert report['spec_qualified'] is False
    assert call_result_tool('query_maestro_results',dict(result_ref=report['report_ref'],entity='rows'),workspace=tmp_path,client=None)['total']==2
    from pathlib import Path
    Path(artifact['path']).write_text('{}')
    with pytest.raises(ResultError):ResultStore(tmp_path).get(ref)
    with pytest.raises(ResultError):store.get('../escape')


def test_native_capture_pagination_release_on_failure_and_no_dispatch_for_report(tmp_path):
    meta,records,_,contract=fixture_data()
    class Client:
        def __init__(self,fail=False):self.calls=[];self.fail=fail
        def call(self,name,args):
            code=args['code'];self.calls.append(code)
            if code.startswith('aiResultsCapture'):
                result={**meta,'ok':True,'pages':2,'history':'CustomHistory.9','context_ref':'design:example'}
            elif code.startswith('aiResultsPage'):
                if self.fail:return False,{'message':'injected page failure'}
                result={'ok':True,'records':records[:3] if code.endswith(' 0)') else records[3:]}
            else:result={'ok':True}
            return True,{'value':json.dumps(result)}
    client=Client();server=McpServer(client,workspace=tmp_path)
    reply=call(server,'read_maestro_results',dict(context_ref='design:example',history='CustomHistory.9'))
    assert not reply['result']['isError'],reply
    detail=json.loads(reply['result']['content'][0]['text']);n=len(client.calls)
    report=call(server,'evaluate_measurement_specs',dict(result_ref=detail['result_ref'],contract=contract))
    assert not report['result']['isError'] and len(client.calls)==n
    client=Client(True)
    with pytest.raises(ResultError):capture(client,'design:example','CustomHistory.9')
    assert client.calls[-1].startswith('aiResultsRelease')


def test_agent_scope_explicit_and_generic_modules_have_no_recipe_imports():
    assert '只是参考校验 recipe' in SERVER_INSTRUCTIONS
    assert '不要从观察结果推断预期覆盖' in SERVER_INSTRUCTIONS
    from pathlib import Path
    for name in ('result_tools.py','result_contract.py'):
        content=(Path(__file__).resolve().parents[1]/'cadai'/name).read_text()
        for forbidden in ('circuit_opamp','circuit_matrix','circuit_gate','gpdk045','Two_Stage_Opamp'):
            assert forbidden not in content
