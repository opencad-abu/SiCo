from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cadai.aivw_tool import AivwArgumentError, run_aivw_recipe


def _client(tmp_path: Path):
    runtime = tmp_path / "runtime"
    spool = runtime / "spool"
    spool.mkdir(parents=True)
    return SimpleNamespace(
        runtime=SimpleNamespace(root=runtime, spool=spool),
        path=runtime / "bridge.sock",
        token="delegated-secret",
    )


def test_aivw_tool_delegates_credentials_only_to_child_environment(tmp_path: Path) -> None:
    entry = tmp_path / "aivw-probe"
    entry.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "assert os.environ['SICO_AI_TOKEN'] == 'delegated-secret'\n"
        "assert os.environ['SICO_AI_WORKSPACE'] == os.getcwd()\n"
        "assert sys.argv[1:4] == ['run-recipe', 'saradc.comparator_new', '--through']\n"
        "print(json.dumps({'status': 'BLOCKED_WORKFLOW', 'run_id': 'probe'}))\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    entry.chmod(0o755)

    result = run_aivw_recipe(
        {"reference": "saradc.comparator_new", "through": "snapshot", "timeout": 5},
        client=_client(tmp_path),
        workspace=tmp_path,
        base_environment={"PATH": "/usr/bin:/bin"},
        entry=entry,
    )

    assert result["exit_code"] == 1
    assert result["aivw"] == {"status": "BLOCKED_WORKFLOW", "run_id": "probe"}
    assert result["delegation"]["credential_exposed_to_agent"] is False
    assert "delegated-secret" not in json.dumps(result)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reference", "../../escape"),
        ("through", "snapshot;touch-bad"),
        ("profile", "/absolute/path"),
    ],
)
def test_aivw_tool_rejects_argument_injection(
    field: str, value: str, tmp_path: Path
) -> None:
    arguments = {
        "reference": "saradc.comparator_new",
        "through": "snapshot",
        "profile": "amsverify",
    }
    arguments[field] = value
    with pytest.raises(AivwArgumentError):
        run_aivw_recipe(
            arguments,
            client=_client(tmp_path),
            workspace=tmp_path,
            entry=tmp_path / "unused",
        )
