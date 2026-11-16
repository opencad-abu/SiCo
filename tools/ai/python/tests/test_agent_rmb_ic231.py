"""Actual editor RMB clicks -> Copilot desktop -> bound get_context on owned X11."""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from agent_gui_support import isolated_display, quick_form, screenshot, wait_until, xdo

CAD = Path(__file__).absolute().parents[3]
IC = Path("/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/virtuoso")
pytestmark = pytest.mark.skipif(
    os.environ.get("SICO_IC_TESTS") != "1" or not shutil.which("tesseract"),
    reason="Opt-in IC231/X11 test with OCR to locate the actual menu item",
)


def menu_labels(path, env):
    """Locate visible labels without depending on native popup height or item order."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QImage

    screenshot(env, path)
    popups = xdo(
        env, "search", "--onlyvisible", "--name", "^(Schematic|Layout|Wire|Shape)$", check=False
    )
    if not popups:
        return []
    geometry = dict(
        line.split("=", 1)
        for line in xdo(env, "getwindowgeometry", "--shell", popups.splitlines()[-1]).splitlines()
    )
    x, y, width, height = (int(geometry[key]) for key in ("X", "Y", "WIDTH", "HEIGHT"))
    original = QImage(str(path))
    enlarged = path.with_stem(path.stem + "-ocr")
    cropped = original.copy(x, y, width, height)
    assert cropped.scaled(width * 3, height * 3, transformMode=Qt.SmoothTransformation).save(
        str(enlarged)
    )
    result = subprocess.run(
        ["tesseract", str(enlarged), "stdout", "--psm", "6", "tsv"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    lines = {}
    for row in csv.DictReader(io.StringIO(result.stdout), delimiter="\t"):
        if row["text"].strip():
            for dimension in ("left", "top", "width", "height"):
                row[dimension] = str(int(row[dimension]) // 3)
            row["left"] = str(int(row["left"]) + x)
            row["top"] = str(int(row["top"]) + y)
            key = tuple(row[k] for k in ("page_num", "block_num", "par_num", "line_num"))
            lines.setdefault(key, []).append(row)
    return list(lines.values())


def label_text(words):
    return " ".join(word["text"] for word in words).lower()


@pytest.mark.parametrize("view", ["schematic", "layout"])
@pytest.mark.parametrize("selected", [False, True], ids=["read-empty", "edit-selected"])
def test_native_rmb_preserves_site_menu_and_bound_target(tmp_path, view, selected):
    source = tmp_path / "startup.il"
    source.write_text(
        f"probeDirectory={json.dumps(str(tmp_path))}\n"
        f"probeLoader={json.dumps(str(CAD / 'sico/skill++/SICO.ils'))}\n"
        f'probeView="{view}" probeType="{"schematic" if view == "schematic" else "maskLayout"}"\n'
        f"probeSelected={'t' if selected else 'nil'}\n"
        f"load({json.dumps(str(Path(__file__).with_name('agent_rmb_fixture.il')))})\n"
    )
    command_number = 0

    def log():
        path = tmp_path / "CDS.log"
        return path.read_text() if path.is_file() else ""

    def command(code):
        nonlocal command_number
        command_number += 1
        path = tmp_path / f"command_{command_number}.il"
        path.with_suffix(".part").write_text(code + f'\nprintf("RMB_STEP_{command_number}\\n")\n')
        path.with_suffix(".part").replace(path)
        wait_until(lambda: f"\\o RMB_STEP_{command_number}\n" in log())

    def events():
        paths = list((tmp_path / ".cad/ai/agent/sessions").glob("*/events.jsonl"))
        if not paths:
            return []
        return [
            json.loads(line)
            for line in paths[0].read_text().splitlines(keepends=True)
            if line.endswith("\n")
        ]

    with isolated_display(tmp_path) as env, (tmp_path / "stdout.log").open("w") as out:
        process = subprocess.Popen(
            [str(IC), "-nocdsinit", "-log", str(tmp_path / "CDS.log"), "-restore", str(source)],
            cwd=tmp_path,
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        try:
            wait_until(lambda: "\\o RMB_READY\n" in log(), 40)
            assert f'RMB_MODE "{"a" if selected else "r"}"' in log(), log()
            editor = xdo(env, "search", "--onlyvisible", "--name", "AgentRmb origin").splitlines()[
                0
            ]
            xdo(env, "windowmove", editor, "0", "0", "windowraise", editor, "windowfocus", editor)
            xdo(env, "mousemove", "--window", editor, "530", "365", "click", "3")

            def popup():
                lines = menu_labels(tmp_path / "rightclick.png", env)
                asks = [line for line in lines if "silicon copilot" in label_text(line)]
                if asks:
                    assert len(asks) == 1, lines
                    assert any("site item" in label_text(line) for line in lines), lines
                    assert any(
                        term in label_text(line)
                        for line in lines
                        for term in ("zoom", "properties", "copy")
                    ), lines
                    return asks[0][0]
                return None

            ask = wait_until(popup, 15)
            command(
                'hiSetCurrentWindow(other)\nprintf("RMB_FOCUS_OTHER %L\\n" hiGetCurrentWindow())'
            )
            xdo(
                env,
                "mousemove",
                str(int(ask["left"]) + 12),
                str(int(ask["top"]) + int(ask["height"]) // 2),
                "click",
                "1",
            )
            quick_form(env, "Read the bound context")
            desktop = wait_until(
                lambda: xdo(
                    env, "search", "--onlyvisible", "--name", "^Silicon Copilot", check=False
                ),
                30,
            ).splitlines()[0]
            xdo(env, "windowmove", desktop, "20", "20", "windowfocus", desktop)

            wait_until(lambda: sum(event["kind"] == "task.completed" for event in events()) == 2)
            starts = [e for e in events() if e["kind"] == "task.started"]
            assert starts[0]["payload"]["origin"] == "startup"
            assert starts[1]["payload"]["text"] == "Read the bound context"
            user_task = starts[1]["task_id"]
            result = next(
                event["payload"]["result"]["data"]
                for event in events()
                if event["kind"] == "tool.finished" and event["task_id"] == user_task
            )
            assert result["valid"] is True
            source = result.get("source", result)
            assert {k: source["cellview"][k] for k in ("lib", "cell", "view_name")} == {
                "lib": "AgentRmb",
                "cell": "origin",
                "view": view,
            }
            assert starts[1]["payload"]["context"]["snapshot"]["entry"] == f"{view}_rmb"
            screenshot(env, tmp_path / "copilot-context.png")
            command(
                'when(probeView=="schematic" unless(siteCalls==1 error("Site command lost")))\n'
                'when(geGetWindowCellView(origin)->mode!="r" dbSave(geGetWindowCellView(origin)))\n'
                "hiCloseWindow(origin)\n"
                "foreach(cid sicoBindings when(sicoBindings[cid]\n"
                "  sicoContextJson(sicoBindings[cid])\n"
                '  when(sicoBindings[cid][\'valid] error("Closed target still valid"))))'
            )
            assert len(list((tmp_path / ".cad/ai/agent/sessions").iterdir())) == 1
        finally:
            (tmp_path / "stop").touch()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        assert "*Error*" not in log(), log()
        assert "Fatal Python error" not in log(), log()
