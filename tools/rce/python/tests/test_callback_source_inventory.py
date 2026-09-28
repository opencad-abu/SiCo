"""Source loading contract for the split SKILL++ callback modules."""

import re
from collections import Counter

import pytest
from skill_style import analyze
from skill_syntax import parse
from skill_test_support import ROOT, rce_callback_paths, rce_source_paths


def test_callback_inventory_matches_development_loader_order():
    paths = rce_callback_paths()
    loader = (ROOT / 'rce/skill++/RCE.ils').read_text()
    names = [path.name for path in paths]
    assert len(names) == len(set(names))
    positions = [loader.index(f'"/skill++/{name}"') for name in names]
    assert positions == sorted(positions)
    assert positions[-1] < loader.index('"/skill++/RCEBATCH.ils"')
    assert loader.count('rceCallbackModulesRevision()=="20260919.callback.modules.v1"') == 2
    definitions = Counter()
    for path in paths:
        assert path.suffix == '.ils'
        source = path.read_text()
        assert len(source.splitlines()) < 300
        _, frames = parse(source)
        assert all(frame.close_token for frame in frames)
        assert analyze(path) == []
        definitions.update(re.findall(r'^procedure\((\w+)\(', source, re.M))
    assert all(count == 1 for count in definitions.values())
    assert len(definitions) == 142


def test_config_serializers_use_explicit_lexical_arguments():
    source = (ROOT / 'rce/skill++/RCETOMLWRITE.ils').read_text()
    caller = (ROOT / 'rce/skill++/RCECONFIGWRITE.ils').read_text()
    signatures = re.findall(r'procedure\((rceWrite\w+Toml)\(([^)]+)\)', source)
    assert len(signatures) == 16
    calls = [f'{name}({arguments})' for name, arguments in signatures]
    assert all(call in caller for call in calls)
    assert [caller.index(call) for call in calls] == sorted(caller.index(call) for call in calls)
    assert caller.index('outFile=outfile(tempFile)') < caller.index(calls[0])
    assert caller.index(calls[-1]) < caller.index('close(outFile)')
    assert caller.index('close(outFile)') < caller.index('system(strcat("mv "')


@pytest.mark.parametrize("group,suffix", [("gui", ".ils"), ("summary", ".il")])
def test_gui_source_groups_preserve_language_order_and_guards(group, suffix):
    paths = rce_source_paths(group)
    loader = (ROOT / "rce/skill++/RCE.ils").read_text()
    offsets = []
    names = Counter()
    for path in paths:
        assert path.suffix == suffix
        source = path.read_text()
        assert len(source.splitlines()) < 300
        assert analyze(path) == []
        _, frames = parse(source)
        assert all(frame.close_token for frame in frames)
        guard = re.search(r"boundp\('([A-Za-z]+Version)\)", source)
        assert guard is not None
        assert f'{guard[1]}="20260919.gui.modules.v1"' in source
        offsets.append(loader.index(f'"/{path.parent.name}/{path.name}"'))
        names.update(re.findall(r"^(?:procedure|defclass|defmethod)\((\w+)", source, re.M))
    assert offsets == sorted(offsets)
    assert all(count == 1 for count in names.values())
    assert len({p.name for p in paths}) == len(paths)
    revision = "rceGuiModulesRevision" if group == "gui" else "rceSummaryModulesRevision"
    assert loader.count(f'{revision}()=="20260919.gui.modules.v1"') >= 2
