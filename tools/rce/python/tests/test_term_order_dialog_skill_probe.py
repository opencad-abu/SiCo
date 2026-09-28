"""Click the actual OA terminal-order dialog in an isolated X display."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest


CAD_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1", reason="set RCE_RUN_SKILL_PROBE=1"
)
def test_term_order_dialog_buttons(tmp_path: Path) -> None:
    virtuoso = shutil.which(os.environ.get("RCE_VIRTUOSO", "virtuoso"))
    xvfb = shutil.which("Xvfb")
    xdotool = shutil.which("xdotool")
    if not all((virtuoso, xvfb, xdotool)):
        pytest.skip("Virtuoso, Xvfb and xdotool are required")
    from PIL import ImageGrab

    replay = tmp_path / "dialog.il"
    replay.write_text(
        f'load("{CAD_ROOT}/rce/skill/RCE_termOrder.il")\n'
        "procedure(rceDialogTestMain()\n"
        'snapshot=makeTable("dialogProbe" nil)\n'
        'snapshot[\'lib]="rce_term_probe" snapshot[\'cell]="top"\n'
        'snapshot[\'view]="schematic"\n'
        'snapshot[\'schematic]=list("Z" "A<1:0>" "VDD!")\n'
        "snapshot['symbolExists]=t\n"
        'snapshot[\'symbol]=list("VDD!" "A<1:0>" "Z")\n'
        "snapshot['orders]=list(list(\"auCdl\" snapshot['symbol])\n"
        '  list("spectre" list("VDD!" "Z" "A<1:0>")))\n'
        "snapshot['viewOrders]=list(list(\"schematic\" snapshot['symbol])\n"
        "  list(\"symbol\" snapshot['symbol]))\n"
        "answer=rceTermOrderPrompt(snapshot)\n"
        'printf("RCE_DIALOG_1: %L\\n" answer)\n'
        "answer=rceTermOrderPrompt(snapshot)\n"
        'printf("RCE_DIALOG_2: %L\\n" answer)\n'
        "answer=rceTermOrderPrompt(snapshot)\n"
        'printf("RCE_DIALOG_3: %L\\n" answer)\n'
        'snapshot[\'reason]="Schematic and symbol have different terminal names."\n'
        "answer=rceTermOrderPrompt(snapshot)\n"
        'printf("RCE_DIALOG_4: %L\\n" answer)\n'
        'exit()\n)\nhiRegTimer("rceDialogTestMain()" 10)\n',
        encoding="utf-8",
    )
    (tmp_path / "cds.lib").write_text("", encoding="utf-8")
    display_file = tmp_path / "display"
    process = None
    log = tmp_path / "dialog.log"
    with (
        display_file.open("w+") as display_stream,
        (tmp_path / "stdout").open("w") as stream,
    ):
        server = subprocess.Popen(
            [
                xvfb,
                "-displayfd",
                str(display_stream.fileno()),
                "-screen",
                "0",
                "1280x900x24",
                "-nolisten",
                "tcp",
            ],
            pass_fds=(display_stream.fileno(),),
            stdout=stream,
            stderr=stream,
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not display_file.read_text().strip():
                time.sleep(0.1)
            display = ":" + display_file.read_text().strip()
            assert display != ":", "Xvfb did not allocate a display"
            env = {**os.environ, "DISPLAY": display, "CDS_LIB": "cds.lib"}
            process = subprocess.Popen(
                [virtuoso, "-nocdsinit", "-replay", str(replay), "-log", str(log)],
                cwd=tmp_path,
                env=env,
                stdout=stream,
                stderr=stream,
            )
            for index, answer in enumerate(
                ("update", "continue", "cancel", "continue"), 1
            ):
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    found = subprocess.run(
                        [
                            xdotool,
                            "search",
                            "--onlyvisible",
                            "--name",
                            "^RCE: OA Terminal Order$",
                        ],
                        env=env,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if found.returncode == 0:
                        break
                    assert process.poll() is None, log.read_text(errors="replace")
                    time.sleep(0.1)
                assert found.returncode == 0, "Terminal order dialog did not appear"
                window = found.stdout.splitlines()[-1]
                geometry = subprocess.check_output(
                    [xdotool, "getwindowgeometry", "--shell", window],
                    env=env,
                    text=True,
                )
                bounds = dict(line.split("=", 1) for line in geometry.splitlines())
                time.sleep(0.2)
                shot = ImageGrab.grab(xdisplay=display)
                x, y = int(bounds["X"]), int(bounds["Y"])
                width, height = int(bounds["WIDTH"]), int(bounds["HEIGHT"])
                shot.crop((x, y, x + width, y + height)).save(
                    tmp_path / f"dialog-{index}.png"
                )
                # Qt starts on the default Cancel button. Traverse backwards
                # to the desired command so the test does not depend on pixels.
                keys = {
                    "update": ["shift+Tab", "shift+Tab", "Return"],
                    "continue": ["shift+Tab", "Return"],
                    "cancel": ["Return"],
                }[answer]
                subprocess.run(
                    [
                        xdotool,
                        "windowfocus",
                        "--sync",
                        window,
                        "key",
                        "--clearmodifiers",
                        *keys,
                    ],
                    env=env,
                    check=True,
                )
                deadline = time.monotonic() + 10
                marker = f"\\o RCE_DIALOG_{index}: {answer}"
                while time.monotonic() < deadline:
                    output = log.read_text(errors="replace") if log.is_file() else ""
                    if marker in output:
                        break
                    time.sleep(0.1)
                assert marker in output, output
            assert process.wait(timeout=15) == 0
            assert "*Error*" not in log.read_text(errors="replace")
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            server.terminate()
            server.wait(timeout=10)
