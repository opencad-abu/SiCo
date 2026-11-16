"""Shared fakes and request fixture for visible-DRC tests."""

import json
from types import SimpleNamespace


class FakeClient:
    def __init__(self, export):
        self.export = export
        self.code = ""

    def call(self, method, arguments):
        assert method == "eval_skill"
        self.code = arguments["code"]
        return True, {"value": json.dumps(self.export)}


class FakeRunner:
    def __init__(self, receipt=None, result=None):
        self.receipt = receipt or SimpleNamespace(
            terminal=True,
            state="violations",
            scope="candidate",
            message="",
            result_snapshot=object(),
        )
        self.result_value = result or {
            "status": "violations",
            "scope": "candidate",
            "signoff_eligible": False,
            "total": 1,
            "checks": {"A": 1},
            "violations": [],
        }
        self.closed = False
        self.submitted = None

    def submit_candidate(self, *arguments, **kwargs):
        self.submitted = (arguments, kwargs)
        return self.receipt

    def status(self, _run_ref):
        return self.receipt

    def result(self, _run_ref):
        return self.receipt, self.result_value

    def cancel(self, _run_ref):
        self.receipt = SimpleNamespace(
            terminal=True,
            state="cancelled",
            scope="candidate",
            message="Calibre job cancelled",
            result_snapshot=None,
        )
        return self.receipt

    def close(self):
        self.closed = True


def request_fixture(tmp_path):
    config = tmp_path / "drc.toml"
    config.write_text("[drc]\n", encoding="utf-8")
    work_root = tmp_path / "work"
    work_root.mkdir()
    calibre = tmp_path / "calibre"
    calibre.mkdir()
    candidate = work_root / "candidate.oasis"
    candidate.write_bytes(b"OASIS\n")
    export = {
        "ok": True,
        "path": str(candidate),
        "size": 6,
        "sha256": "a" * 64,
        "source": {"cell": "inv", "view": "layout"},
        "visible_bbox": [0, 0, 100, 100],
        "warnings": ["visible_fragment"],
        "counts": {"output_shapes": 4},
        "connectivity_preserved": False,
        "lvs_eligible": False,
    }
    flow = SimpleNamespace(
        top_cell="inv",
        layer_map=SimpleNamespace(path=str(tmp_path / "layer.map")),
        runset_identity=SimpleNamespace(dependencies_sha256="b" * 64),
    )
    arguments = {
        "window_ref": "wctx_fixture_1",
        "config_path": str(config),
        "work_root": str(work_root),
        "calibre_root": str(calibre),
        "timeout_seconds": 10,
    }
    return arguments, export, flow, candidate, work_root


__all__ = ["FakeClient", "FakeRunner", "request_fixture"]
