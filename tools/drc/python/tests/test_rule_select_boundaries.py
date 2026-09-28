"""Dependency, compatibility and publication boundaries of rule selection."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from drcpy.rule_select import discover_rule_groups


def test_headless_entry_and_selection_protocol_do_not_import_qt() -> None:
    source = '''
import sys
from drcpy import cli, rule_groups, rule_select, rule_select_config, rule_selection
assert not any(name.startswith("PyQt5") for name in sys.modules)
assert rule_select.RuleGroupInfo is rule_groups.RuleGroupInfo
assert rule_select.parse_rule_groups is rule_groups.parse_rule_groups
assert rule_select.selected_rule_names is rule_select_config.selected_rule_names
assert rule_select.selected_rule_groups is rule_select_config.selected_rule_groups
assert rule_selection.RuleSelection(("M1",), ()).groups == ("M1",)
'''
    completed = subprocess.run(
        [sys.executable, '-c', source], capture_output=True, text=True,
        env={**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)}, timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


def test_legacy_gui_exports_reference_their_owners() -> None:
    from drcpy import rule_select_gui as legacy
    from drcpy import rule_select_dialog, rule_select_model, rule_select_view
    from drcpy import rule_select_lifecycle, rule_select_style, rule_selection

    for owner, names in (
        (rule_select_dialog, ('RuleSelectDialog',)),
        (rule_select_model, ('RuleSelectModel',)),
        (rule_select_view, ('RuleSelectFilterProxyModel',)),
        (rule_select_lifecycle, ('capture_parent_identity',)),
        (rule_select_style, ('apply_application_style',)),
        (rule_selection, ('RuleSelection', 'read_initial_selection', 'write_selection')),
    ):
        for name in names:
            assert getattr(legacy, name) is getattr(owner, name)


@pytest.mark.parametrize('failure', ['timeout', 'exit', 'missing', 'publish'])
def test_expansion_failure_removes_partial_output_without_touching_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    rule = tmp_path / 'rules.tvf'
    rule.write_text('#! tvf\nGROUP STATIC M1\nM1 { COPY M1 }\n')
    cache = tmp_path / 'cache'
    cache.mkdir()
    existing = cache / 'unrelated.svrf'
    existing.write_text('keep\n')

    def run(command, **kwargs):
        output = Path(command[2])
        if failure != 'missing':
            output.write_text('GROUP EXPANDED M2\nM2 { COPY M2 }\n')
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        return subprocess.CompletedProcess(command, 2 if failure == 'exit' else 0, 'failed')

    def publish(*args):
        raise OSError('cache publication unavailable')

    monkeypatch.setattr('drcpy.rule_expansion.subprocess.run', run)
    if failure == 'publish':
        monkeypatch.setattr('drcpy.rule_expansion.os.replace', publish)
    result = discover_rule_groups(rule, cache_dir=cache)

    assert result.source == 'static'
    assert result.groups == ('STATIC',)
    assert result.members == {'STATIC': ('M1',)}
    assert result.error
    assert sorted(cache.iterdir()) == [existing]
    assert existing.read_text() == 'keep\n'
