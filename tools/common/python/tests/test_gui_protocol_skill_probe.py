from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from cadgui.protocol import TransferDocument, read_transfer, write_transfer


CAD_ROOT = Path(__file__).resolve().parents[3]


def _skill_string(value: str | Path) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def test_skill_and_python_share_the_gui_transfer_protocol(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    input_path = tmp_path / "python.tsv"
    output_path = tmp_path / "skill.tsv"
    write_transfer(
        input_path,
        (("GROUP", "METAL"), ("CHECK", "M1.WIDTH")),
        status="applied",
        version="1",
    )
    input_path.write_text(
        "# transfer comment\n"
        + input_path.read_text(encoding="utf-8").replace(
            "GROUP\tMETAL\n", "#feature\tfuture\nGROUP\tMETAL\n"
        ),
        encoding="utf-8",
    )
    malformed_contents = (
        "GROUP\tMETAL\t\n",
        "GROUP\t\tMETAL\n",
        "\tGROUP\tMETAL\n",
        "GROUP\t\n",
        "\tMETAL\n",
        "\t\n",
        "GROUP\tMETAL\tEXTRA\n",
    )
    malformed_paths = []
    for index, content in enumerate(malformed_contents):
        malformed_path = tmp_path / f"malformed-{index}.tsv"
        malformed_path.write_text(content, encoding="utf-8")
        malformed_paths.append(malformed_path)
    crlf_path = tmp_path / "crlf.tsv"
    crlf_path.write_bytes(b"# comment\r\nGROUP\tMETAL\r\n")
    toml = CAD_ROOT / "common/skill/SICO_toml.il"
    protocol = CAD_ROOT / "common/skill/SICO_guiProtocol.il"
    skill_lines = [
        f"load({_skill_string(toml)})",
        f"load({_skill_string(protocol)})",
        "doc=SICO_guiProtocolRead("
        f'{_skill_string(input_path)} list("GROUP" "CHECK") "applied")',
        'if(and(car(doc) caddr(doc)=="applied" cadddr(doc)=="1"',
        '       equal(cadr(doc) list(list("GROUP" "METAL")',
        '                            list("CHECK" "M1.WIDTH"))))',
        '  then printf("CAD_GUI_PROTOCOL_READ_OK\\n"))',
        "writeOk=SICO_guiProtocolWrite("
        f"{_skill_string(output_path)} "
        'list(list("GROUP" "VIA") list("CHECK" "V1.SPACE")) '
        '"applied" "1")',
        'when(writeOk printf("CAD_GUI_PROTOCOL_WRITE_OK\\n"))',
        'environment=SICO_guiPythonEnvironment("DRC_ORIG_LD_LIBRARY_PATH")',
        'if(and(environment rexMatchp("-u QT_XCB_NO_XI2" environment)',
        '       rexMatchp("DRC_ORIG_LD_LIBRARY_PATH=" environment))',
        '  then printf("CAD_GUI_ENVIRONMENT_OK\\n"))',
    ]
    for index, malformed_path in enumerate(malformed_paths):
        skill_lines.extend(
            (
                "doc=SICO_guiProtocolRead("
                f'{_skill_string(malformed_path)} list("GROUP" "CHECK"))',
                f'when(!car(doc) printf("CAD_GUI_PROTOCOL_REJECT_{index}\\n"))',
            )
        )
    skill_lines.extend(
        (
            "doc=SICO_guiProtocolRead("
            f'{_skill_string(crlf_path)} list("GROUP" "CHECK"))',
            'when(and(car(doc) equal(cadr(doc) list(list("GROUP" "METAL"))))',
            '  printf("CAD_GUI_PROTOCOL_CRLF_OK\\n"))',
            "exit()",
        )
    )
    skill = "\n".join(skill_lines)
    completed = subprocess.run(
        [dbaccess],
        input=skill,
        text=True,
        capture_output=True,
        timeout=40,
        check=False,
        cwd=tmp_path,
    )
    output = completed.stdout + completed.stderr

    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "(reader)" not in output
    assert "still unclosed on EOF" not in output
    assert "CAD_GUI_PROTOCOL_READ_OK" in output
    assert "CAD_GUI_PROTOCOL_WRITE_OK" in output
    assert "CAD_GUI_ENVIRONMENT_OK" in output
    for index in range(len(malformed_paths)):
        assert f"CAD_GUI_PROTOCOL_REJECT_{index}" in output
    assert "CAD_GUI_PROTOCOL_CRLF_OK" in output
    assert read_transfer(output_path) == TransferDocument(
        (("GROUP", "VIA"), ("CHECK", "V1.SPACE")),
        status="applied",
        version="1",
    )
