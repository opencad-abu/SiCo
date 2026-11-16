"""Licensed finite CDF adapter acceptance; every auxiliary object stays in SicoTest."""

import json
import os
import shlex
import subprocess
import uuid
from pathlib import Path

import pytest
from sico.adapters.circuit import CircuitClient
from sico.interpreter import cad_python
from sico.transport.broker import ContextBroker

from cadai.pdk_bridge import PdkBridge
from cadai.pdk_standard.cdf_validation_native import Native
from cadai.pdk_standard.cdf_validation_result import classify

REPO = Path(__file__).resolve().parents[4]
IC = Path("/software/eda/cadence/installs/IC23.10.130")
pytestmark = pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_CDF_TESTS") != "1" or not os.environ.get("CAD_AI_CDF_WORKSPACE"),
    reason="licensed CDF acceptance requires an explicit current project workspace",
)


@pytest.fixture
def connected(tmp_path):
    workspace = Path(os.environ["CAD_AI_CDF_WORKSPACE"]).resolve()
    with ContextBroker(30, operation_timeout=45) as broker:
        config = tmp_path / "connection.json"
        config.write_text(
            json.dumps(dict(host=broker.address[0], port=broker.address[1], token=broker.token))
        )
        config.chmod(0o600)
        command = shlex.join(
            [
                cad_python(),
                "-s",
                str(REPO / "tools/sico/python/sico-entry"),
                "relay",
                "--connection-file",
                str(config),
            ]
        )
        stop = tmp_path / "stop"
        startup = tmp_path / "startup.il"
        startup.write_text(
            "\n".join(
                [
                    f"load({json.dumps(str(REPO / 'tools/sico/skill++/SICO.ils'))})",
                    'p03NativeSource=makeTable("p03NativeSource" nil)',
                    f'sicoConnect({json.dumps(command)} "p03-native" "generation" '
                    "nil p03NativeSource)",
                    "procedure(p03NativeExit() "
                    f"if(isFile({json.dumps(str(stop))}) then exit() "
                    'else hiRegTimer("p03NativeExit()" 1)))',
                    'hiRegTimer("p03NativeExit()" 1)',
                ]
            )
            + "\n"
        )
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"PYTHONHOME", "PYTHONPATH", "LD_PRELOAD", "LD_LIBRARY_PATH"}
            and not k.startswith("QT_")
        }
        env.update(
            PROJ_HOME=str(workspace.parent),
            CDSHOME=str(IC),
            SICO_HOME=str(REPO),
            SICO_ROOT=str(REPO / "tools/ai"),
        )
        with (tmp_path / "stdout.log").open("w") as output:
            process = subprocess.Popen(
                [
                    str(IC / "tools/dfII/bin/virtuoso"),
                    "-nographE",
                    "-nocdsinit",
                    "-cdslib",
                    str(workspace / "cds.lib"),
                    "-restore",
                    str(startup),
                    "-log",
                    str(tmp_path / "CDS.log"),
                ],
                cwd=workspace,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        try:
            context = broker.context("p03-native", timeout=40)

            def skill(code):
                result = broker.assistant_call(context, "eval_skill_native", code)
                assert result["ok"], result
                detail = result.get("result", result)
                assert detail["ok"], detail
                return detail.get("value")

            client = CircuitClient(broker, context, workspace)
            yield Native(PdkBridge(client), workspace), skill
        finally:
            stop.touch()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def test_native_type_trace_transition_failures_and_capture_release(connected, tmp_path):
    native, skill = connected
    token = uuid.uuid4().hex[:16]
    cell = "pdk_cdf_fixture_" + token
    skill(
        "progn(aiTestWorkspaceLibrary() "
        # Synthetic CDF lives in the auxiliary library; no vendor initializer exists.
        "p03NativeOriginalInit=getd('aiPdkInitLibrary) "
        "procedure(aiPdkInitLibrary(p03Lib) "
        'if(p03Lib=="SicoTest" list(t nil nil) apply(p03NativeOriginalInit list(p03Lib)))) '
        "procedure(p03NativeCallback() "
        'when(cdfgData->w->value=="-1" error("deliberate rejection")) '
        'when(cdfgData->w->value=="0" cdfgData->w->value="1") '
        "cdfgData->derived->value=cdfgData->mode->value t) "
        f'when(ddGetObj("SicoTest" "{cell}") error("fixture conflict")) '
        f'p03NativeMaster=dbOpenCellViewByType("SicoTest" "{cell}" "symbol" "schematicSymbol" "a") '
        'dbCreateRect(p03NativeMaster list("device" "drawing") list(-0.5:-0.5 0.5:0.5)) '
        "dbSave(p03NativeMaster) dbClose(p03NativeMaster) "
        f'p03NativeCdf=cdfCreateBaseCellCDF(ddGetObj("SicoTest" "{cell}")) '
        'cdfCreateParam(p03NativeCdf ?name "w" ?prompt "w" ?type "string" ?defValue "1" '
        '?callback "p03NativeCallback()") '
        'cdfCreateParam(p03NativeCdf ?name "mode" ?prompt "mode" ?type "cyclic" ?defValue "a" '
        '?choices list("a" "b") ?callback "p03NativeCallback()") '
        'cdfCreateParam(p03NativeCdf ?name "derived" ?prompt "derived" '
        '?type "string" ?defValue "a") '
        'cdfCreateParam(p03NativeCdf ?name "bad" ?prompt "bad" ?type "string" '
        '?defValue "1" ?callback "eval(1)") '
        "cdfSaveCDF(p03NativeCdf) t)"
    )
    initial = skill("aiCdfCaptureCount")
    results = []
    for key, value, transition, expected in [
        ("baseline", "1", None, "accepted"),
        ("adjusted", "0", None, "adjusted"),
        ("rejected", "-1", None, "rejected"),
        ("transition", "1", dict(mode="b", w="2"), "accepted"),
        ("unsupported", "1", None, "unsupported"),
    ]:
        master = dict(library="SicoTest", cell=cell, view="symbol")
        inspected = native.inspect(master, ["w", "mode", "derived", "bad"])
        (tmp_path / (key + "-inspection.json")).write_text(json.dumps(inspected, indent=2))
        case = dict(parameters=dict(mode="a", w=value), observe=["w", "mode", "derived"])
        order = ["mode", "w"]
        if transition:
            case["transition"] = transition
        if key == "unsupported":
            case["parameters"] = dict(bad="1")
            order = ["bad"]
        target = dict(library="SicoTest", cell="pdk_cdf_" + token + "_" + key, view="schematic")
        result = native.probe(inspected["cdf_ref"], case, order, target, token + key, inspected)
        (tmp_path / (key + "-probe.json")).write_text(json.dumps(result, indent=2))
        classified = classify(result, case, {})
        assert classified["status"] == expected, result
        assert result["window_open"] is False
        assert skill("aiCdfCaptureCount") == initial
        if transition:
            assert "transition:after" in classified["trace_labels"]
            assert next(r["value"] for r in classified["observed"] if r["name"] == "derived") == "b"
        results.append(result)
    (tmp_path / "native-evidence.json").write_text(json.dumps(results, indent=2))
