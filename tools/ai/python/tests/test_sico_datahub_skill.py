"""DataHub SKILL edge roundtrips, limits and SICO_quick request mapping."""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
DB = Path("/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/dbAccess")
pytestmark = pytest.mark.skipif(not DB.is_file(), reason="IC23 dbAccess is not installed")


def run_skill(tmp_path, body, *, quick=False):
    source = tmp_path / "datahub-skill.il"
    loads = [
        ROOT / "tools/ai/skill/AI_protocol.il",
        ROOT / "tools/ai/skill/AI_copilot_literals.il",
        ROOT / "tools/ai/skill/AI_pdk_core.il",
        ROOT / "tools/ai/skill/AI_datahub_contracts.il",
        ROOT / "tools/ai/skill/AI_datahub.il",
    ]
    if quick:
        loads.append(ROOT / "tools/sico/skill/SICO_quick.il")
    source.write_text(
        "cadContextBuilding=t\n"
        + "".join(f"load({json.dumps(str(path))})\n" for path in loads)
        + body
        + '\nprintf("SKILL_DATAHUB_PASS\\n") exit()\n'
    )
    env = {
        key: value for key, value in os.environ.items()
        if key not in {"LD_LIBRARY_PATH", "LD_PRELOAD", "SICO_ROOT"}
    }
    result = subprocess.run(
        [str(DB), "-load", str(source)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr
    (tmp_path / "output.log").write_text(output)
    assert "SKILL_DATAHUB_PASS" in output, output
    assert "*Error*" not in output and "*WARNING*" not in output, output


def test_skill_envelope_contract_errors_and_legacy_adapter(tmp_path):
    run_skill(tmp_path, r'''
resolved=datahubResolve("sico.cad.objects.v1" 1 "list")
unless(car(resolved)=='ok error("Contract did not resolve"))
payload=aiPdkObj(list(
  list("window_ref" "wctx_fixture_1")
  list("kind" "all")
  list("query" "")
  list("offset" 0)
  list("limit" 50)))
checked=datahubCheckRequest("sico.cad.objects.v1" 1 "list" payload
  list("aiSelectionList"))
unless(car(checked)=='ok error("Valid request rejected"))
missing=datahubCheckRequest("sico.cad.objects.v1" 1 "list"
  aiPdkObj(list(
    list("window_ref" "wctx_fixture_1")
    list("kind" "all")
    list("offset" 0)
    list("limit" 50))) list("aiSelectionList"))
unless(cadr(missing)=="invalid_arguments"
  error("Missing empty query was accepted"))
wrong=datahubCheckRequest("sico.cad.objects.v1" 1 "list" payload
  list("aiWindowList"))
unless(cadr(wrong)=="capability_not_available"
  error("Wrong capability was accepted"))
version=datahubResolve("sico.cad.objects.v1" 2 "list")
unless(cadr(version)=="version_mismatch"
  error("Version mismatch was not reported"))
unknownAction=datahubResolve("sico.cad.objects.v1" 1 "missing")
unless(cadr(unknownAction)=="unsupported_contract"
  error("Unknown action was not reported"))
envelope=datahubOk("sico.cad.objects.v1" 1 "objects-1" "list"
  aiPdkObj(list(
    list("session_ref" "fixture")
    list("window_ref" "wctx_fixture_1")
    list("target" aiPdkObj(list(
      list("library" "worklib")
      list("cell" "OTA")
      list("view_name" "schematic")
      list("oa_view_type" "schematic")
      list("oa_view_type_source" "platform_api"))))
    list("items" aiPdkArray(nil))
    list("selection_count" 0)))
  datahubPage(0 50 0 nil nil) datahubMeta())
unless(rexMatchp("\"hub\":\"sico.datahub.v1\"" datahubJson(envelope))
  error("Envelope identity missing"))
legacy=datahubLegacyJson(envelope)
unless(rexMatchp("\"schema\":\"cad.cv.objects.v1\"" legacy)
  error("Legacy schema missing"))
unless(rexMatchp("\"total\":0" legacy)
  error("Legacy pagination missing"))
errorEnvelope=datahubError("sico.cad.highlight.v1" 1 "e1" "highlight"
  "unsupported_object" "bad object")
unless(rexMatchp("\"code\":\"invalid_arguments\"" datahubJson(errorEnvelope))
  error("Stable error code missing"))
unless(rexMatchp("\"legacy_code\":\"unsupported_object\""
  datahubJson(errorEnvelope)) error("Legacy error code missing"))
unless(rexMatchp("\"code\":\"unsupported_object\""
  datahubLegacyJson(errorEnvelope)) error("Legacy error was not restored"))
unknown=datahubUnknown("sico.cad.objects.v1" 1 "u1" "list")
unless(rexMatchp("\"code\":\"timeout\"" datahubLegacyJson(unknown))
  error("Unknown outcome was not isolated"))
''')


def test_skill_payload_and_envelope_limits(tmp_path):
    run_skill(tmp_path, r'''
large="x"
for(index 1 10 large=strcat(large large))
items=nil
for(index 1 200 items=cons(large items))
payload=aiPdkObj(list(
  list("session_ref" "fixture")
  list("window_ref" "wctx_fixture_1")
  list("target" aiPdkObj(nil))
  list("items" aiPdkArray(items))
  list("selection_count" 200)))
envelope=datahubOk("sico.cad.objects.v1" 1 "large" "list" payload
  datahubPage(0 50 200 50 t) datahubMeta())
unless(null(errset(datahubJson(envelope) nil))
  error("Oversized payload was accepted"))
unless(rexMatchp("\"code\":\"result_too_large\""
  datahubLegacyResult(envelope "too large"))
  error("Native limit error was not mapped"))
unless(null(errset(datahubOk("sico.cad.highlight.v1" 1 "bad-page" "highlight"
  aiPdkObj(nil) datahubPage(0 50 1 nil nil) datahubMeta()) nil))
  error("Contract page limit was not enforced"))
huge="x"
for(index 1 17 huge=strcat(huge huge))
details=aiPdkObj(list(list("value" huge)))
errorEnvelope=datahubError("sico.cad.objects.v1" 1 "large-error" "list"
  "internal" "failed" details)
unless(null(errset(datahubJson(errorEnvelope) nil))
  error("Oversized envelope was accepted"))
''')


def test_sico_quick_normalizes_empty_query_and_routes_errors(tmp_path):
    run_skill(tmp_path, r'''
procedure(aiSelectionInspect(qorRef qorOffset qorLimit)
  "{\"ok\":true,\"items\":[]}")
procedure(aiSelectionList(qorRef qorKind qorQuery qorOffset qorLimit)
  capturedQuery=qorQuery
  "{\"ok\":true,\"items\":[]}")
procedure(aiWindowList(qorOffset qorLimit)
  "{\"ok\":true,\"windows\":[]}")
putd('sicoQuickSend lambda((composer kind @optional message)
  composer['notices]=append1(composer['notices] kind)
  composer['message]=message t))
composer=makeTable("datahubQuick" nil)
sicoQuickObjects(composer "objects o1 wctx_fixture_1 all 0 50 ")
unless(and(composer['notices]==list("objects") capturedQuery=="")
  error("Six-field empty query did not normalize"))
composer['notices]=nil
sicoQuickObjects(composer "objects o2 wctx_fixture_1 all 0 50 -")
unless(and(composer['notices]==list("objects") capturedQuery=="")
  error("Dash empty query did not normalize"))
composer['notices]=nil
sicoQuickObjects(composer "objects o3 wctx_fixture_1 all 0 50 564f5554")
unless(and(composer['notices]==list("objects") capturedQuery=="VOUT")
  error("Encoded query was not decoded"))
composer['notices]=nil
sicoQuickObjects(composer "selection o4 wctx_fixture_1 0 50")
unless(composer['notices]==list("objects") error("Selection was not returned"))
composer['notices]=nil
sicoQuickObjects(composer "windows o5 0 200")
unless(composer['notices]==list("objects") error("Windows were not returned"))
composer['notices]=nil
sicoQuickObjects(composer "bogus o6 x 0 50")
unless(and(composer['notices]==list("objects")
           rexMatchp("\"code\":\"invalid_arguments\"" composer['message]))
  error("Invalid operation did not use the hub error path"))
''', quick=True)
