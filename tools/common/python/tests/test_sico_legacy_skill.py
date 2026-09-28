"""Retired SKILL names remain inventoried and absent from fresh runtime callers."""

import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[4]
LEGACY = ROOT / 'tools/common/skill/SICO_legacyFunctions.il'
REGISTRY = ROOT / 'tools/utility/compatibility/sico-skill.json'


def symbols():
    return {old: new for row in json.loads(REGISTRY.read_text())['compatibility']
            for old, new in row['symbols'].items()}


def test_every_retired_function_has_one_current_owner():
    assert not LEGACY.exists()
    for row in json.loads(REGISTRY.read_text())['compatibility']:
        assert row['status'] == 'removed' and not row['allow_exposure']
        owner = (ROOT / row['owner']).read_text()
        for new in row['symbols'].values():
            assert re.search(r'procedure\(' + new + r'\(', owner)


def test_current_skill_callers_do_not_return_to_legacy_functions():
    paths = subprocess.check_output(['git', 'ls-files', '-co', '--exclude-standard', '-z'],
                                    cwd=ROOT).decode().split('\0')
    pattern = re.compile(r'\bCAD_[A-Za-z]\w*\b')
    violations = []
    legacy_names = set(symbols())
    for name in set(paths):
        path = ROOT / name
        if not name.startswith('tools/') or path.suffix not in ('.il', '.ils'):
            continue
        if path == LEGACY or '/tests/' in name or '/testdata/' in name:
            continue
        if not path.exists():
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            # Environment names remain presence-only retirement diagnostics.
            old = [m for m in pattern.findall(line.split(';', 1)[0]) if m in legacy_names]
            if old:
                violations.append((name, number, old))
    assert not violations


def test_retired_source_loaders_cannot_be_loaded():
    registry = json.loads((ROOT / 'tools/utility/compatibility/sico-loaders.json').read_text())
    for row in registry['compatibility']:
        assert row['status'] == 'removed'
        assert not (ROOT / row['source']).exists()


def test_fresh_skill_process_exposes_only_current_functions(tmp_path):
    executable = shutil.which('dbAccess')
    if not executable:
        pytest.skip('dbAccess unavailable')
    lines = [f'load("{ROOT}/tools/common/skill/SICO_toml.il")']
    for old in symbols():
        lines.append(f"when(isCallable('{old}) error(\"retired callable {old}\"))")
    lines += ['unless(SICO_shellQuote("space value")=="\'space value\'" error("quoting"))',
              'printf("SICO_RETIRED_FUNCTIONS_ABSENT\\n")']
    probe = 'unless(errset(progn(\n' + '\n'.join(lines) + ') t) exit(1))\nexit(0)\n'
    result = subprocess.run([executable], input=probe, cwd=tmp_path, text=True,
                            capture_output=True, timeout=60)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and '*Error*' not in output, output
    assert 'SICO_RETIRED_FUNCTIONS_ABSENT' in output, output
