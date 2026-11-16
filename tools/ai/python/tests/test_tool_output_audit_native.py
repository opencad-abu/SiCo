"""Real SiCo PDK/CDF captures followed by reads in the same owned Virtuoso."""

import json
import os

import pytest
from test_cdf_callbacks_native import native

from sico.core.contracts import ToolCall


pytestmark = pytest.mark.skipif(os.environ.get("SICO_AI_RUN_CDF_TESTS") != "1",
                                reason="licensed isolated tool output acceptance")


def test_public_pdk_and_cdf_diagnostics(native, tmp_path):
    skill, call, registry, context = native
    skill('progn(oaLibrary=getd(\'aiPdkLibrary) '
          'procedure(oaReadLibrary(oaLib) printf("PDK_LIBRARY_READ\\n") '
          'warn("PDK_LIBRARY_WARNING\\n") warn("PDK_BUFFERED\\n") '
          'apply(oaLibrary list(oaLib))) '
          'putd(\'aiPdkLibrary getd(\'oaReadLibrary)) '
          'oaPo=poport oaWo=woport oaErr=errport t)')
    prepared = call("get_pdk_preparation", dict(include_base_libraries=True))
    assert "PDK_LIBRARY_READ\n" in prepared["output"]
    assert "PDK_LIBRARY_WARNING" in prepared["output"]
    assert prepared["output_capture"] == "skill_ports" and not prepared["output_truncated"]
    # Native capture failures must preserve the original SKILL reason in the public reply.
    skill('progn(oaContext=getd(\'aiPdkContext) '
          'procedure(oaBadContext(@rest oaArgs) printf("PDK_BEFORE_FAILURE\\n") '
          'warn("PDK_LAST_WARNING\\n") error("PDK_CONTEXT_FAILURE")) '
          'putd(\'aiPdkContext getd(\'oaBadContext)) t)')
    failed = registry.execute(ToolCall("failed", "get_pdk_preparation", {}), context)
    assert failed.status == "tool_error", failed.record()
    assert "PDK_CONTEXT_FAILURE" in failed.data["message"]
    assert "PDK_BEFORE_FAILURE\n" in failed.data["output"]
    assert "*Error* PDK_CONTEXT_FAILURE" in failed.data["output"]
    assert "PDK_LAST_WARNING" in failed.data["output"]
    skill('progn(putd(\'aiPdkContext oaContext) putd(\'aiPdkLibrary oaLibrary) '
          'oaSnapshot=getd(\'aiCdfSnapshot) '
          'procedure(oaReadCdf(@rest oaArgs) printf("CDF_READ\\n") '
          'warn("CDF_READ_WARNING\\n") apply(oaSnapshot oaArgs)) '
          'putd(\'aiCdfSnapshot getd(\'oaReadCdf)) t)')
    cdf = call("inspect_cdf", dict(target=dict(library="analogLib", cell="res", view="symbol")))
    assert "CDF_READ\n" in cdf["output"] and "CDF_READ_WARNING" in cdf["output"]
    page = call("inspect_cdf", dict(cdf_ref=cdf["cdf_ref"], parameters=["r"]))
    assert page["output"] == ""
    invalid = registry.execute(ToolCall("missing", "inspect_cdf", dict(
        target=dict(library="cdfWork", cell="absent", view="symbol"))), context)
    assert invalid.status == "tool_error"
    assert "CDF target does not exist" in invalid.data["output"]
    assert skill('and(poport==oaPo woport==oaWo errport==oaErr !getWarn())') == "t"
    (tmp_path / "audit.json").write_text(json.dumps(dict(preparation=prepared,
        failure=failed.data, cdf=cdf, invalid=invalid.data), ensure_ascii=False))
    for name in ("stdout.log", "CDS.log"):
        log = (tmp_path / name).read_text()
        assert "*Error* PDK_CONTEXT_FAILURE" not in log
        assert "*WARNING* CDF_READ_WARNING" not in log
