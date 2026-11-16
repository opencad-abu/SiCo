"""The independent AI tool can use Responses adaptation without the SiCo app."""

import os
from pathlib import Path
import subprocess
import sys


def test_responses_proxy_imports_and_runs_with_agent_packages_denied(tmp_path):
    source = Path(__file__).resolve().parents[1]
    common = source.parents[1] / "common/python"
    code = '''
import importlib.abc, sys
class DenyAgent(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'cadai_agent', 'cadai_assistant_ui', 'sico', 'sico_ui'}:
            raise AssertionError('independent tool imported app: ' + fullname)
sys.meta_path.insert(0, DenyAgent())
from cadai.codex_launch import build_codex_command
from cadai.responses_wrapper import ResponsesWrapper
from cadai.responses_tools import ToolNames
from cadai.json_values import strict_json
assert strict_json('{"ok":true}') == {'ok': True}
names = ToolNames()
assert names.encode('virtuoso', 'read') == 'virtuoso__read'
with ResponsesWrapper('http://127.0.0.1:1/v1', 'test-key') as wrapper:
    assert wrapper.base_url.startswith('http://127.0.0.1:')
'''
    result = subprocess.run([sys.executable, "-s", "-c", code], cwd=tmp_path,
                            env={**os.environ, "PYTHONPATH": os.pathsep.join((str(source), str(common)))},
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
