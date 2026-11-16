"""The Virtuoso adapter reuses only the captured bridge's live project desktop."""

import pytest

from test_agent_quick_host_skill import run_skill


def test_registry_skips_stopping_dead_foreign_generation_and_project(tmp_path):
    run_skill(tmp_path, r'''
sicoBindings=makeTable("bindings" nil)
bridge=makeTable("bridge" nil)
bridge['instanceId]="instance" bridge['generation]="current" bridge['cwd]="/tmp"
foreach(name list("stopping" "dead" "old" "foreign" "project" "valid")
  row=makeTable("desktop" nil) row['desktopSession]=t
  foreach(field list('instanceId 'generation 'cwd) row[field]=bridge[field])
  sicoBindings[name]=row)
sicoBindings["stopping"]['stopping]=t
sicoBindings["old"]['generation]="old"
sicoBindings["foreign"]['instanceId]="foreign"
sicoBindings["project"]['cwd]="/other/project"
procedure(sicoSessionLive(cid) cid!="dead")
unless(sicoDesktopFind(bridge)=="valid" error("Registry reused an unrelated desktop"))
sicoBindings["valid"]=nil
when(sicoDesktopFind(bridge) error("Registry ignored lifecycle or bridge identity"))
''')


@pytest.mark.parametrize("replacement", ["new", "old"])
def test_delayed_old_bridge_wake_is_visible_without_changing_durable_acceptance(tmp_path, replacement):
    run_skill(tmp_path, r'''
sicoBindings=makeTable("bindings" nil) sicoInstanceBridge="old"
sicoBindings["old"]=makeTable("old" nil)
form=hostForm("late" "child")
''' + f'sicoInstanceBridge="{replacement}"\n' + r'''
sicoBindings[sicoInstanceBridge]=makeTable("replacement" nil)
procedure(sicoDesktopFind(bridge) error("Old wake reached the new registry"))
sicoQuickHostData("child" strcat("@wake late "
 "33333333333333333333333333333333 11111111111111111111111111111111 "
 "22222222222222222222222222222222\n@accepted late\n"))
unless(form['notices]==list("accepted") error("Old wake changed acceptance"))
''', expected_error="Silicon Copilot: captured Ask bridge unavailable.")
