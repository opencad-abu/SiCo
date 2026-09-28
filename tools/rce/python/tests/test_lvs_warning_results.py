from __future__ import annotations

import json
from pathlib import Path
import shlex
import sys

import pytest

from cadbatch.controller import BatchController
from cadbatch.manifest import BatchManifest, TaskSpec
from cadbatch.status import finalize_publications
from rcepy.runner import RceRunner, Stage
from test_extract_stage_outputs import _install_fake_tool
from test_native_view_qrc import _config
from xrc_test_support import install_fake_calibre, make_xrc_config


@pytest.mark.parametrize("extraction_fails", [False, True])
def test_native_output_preserves_ignored_lvs_warning(
    tmp_path, monkeypatch, extraction_fails
):
    cfg = _config(tmp_path)
    cfg = cfg.replace('lvs', 'ignore_error', value=True)
    rule = tmp_path / "lvs.cal"
    rule.write_text("// fake LVS rule\n")
    cfg = cfg.replace('lvs', 'runset_file', value=str(rule))
    runner = RceRunner(cfg)
    lvs = Stage(
        "lvs",
        ["/bin/sh", "-c", "echo 'LVS completed. INCORRECT.'; exit 4"],
        runner.ctx.run_dir,
        runner.ctx.log_dir / "callvs.log",
        check="lvs",
    )
    _install_fake_tool(tmp_path, monkeypatch, "qrc", "no_output")
    extract = runner._extract_stage()
    if extraction_fails:
        extract = Stage("extract", ["/bin/false"], extract.cwd, extract.log_file)
    monkeypatch.setattr(runner, "_stages", lambda: [lvs, extract])
    if extraction_fails:
        with pytest.raises(RuntimeError, match="extract failed"):
            runner.run()
    else:
        assert runner.run() == 0
    assert (
        runner.ctx.log_dir / "lvs-ignored-mismatch"
    ).is_file() is not extraction_fails


@pytest.mark.parametrize("publication", [None, "succeeded", "failed"])
@pytest.mark.parametrize("flow", ["RCE", "DRC"])
def test_batch_preserves_rce_warning_through_publication(tmp_path, publication, flow):
    run = tmp_path / "run"
    (run / "log").mkdir(parents=True)
    (run / "log/lvs-ignored-mismatch").write_text("LVS mismatch accepted\n")
    manifest = BatchManifest(
        path=tmp_path / "batch.toml",
        schema_version=1,
        batch_id="warnings",
        flow=flow,
        parallel_cells=1,
        temp_dir=tmp_path / ".cad",
        status_json=tmp_path / "status.json",
        status_tsv=tmp_path / "status.tsv",
        tasks=(
            TaskSpec(
                index=1,
                task_id="001",
                label="top",
                command="true",
                cancel_command="",
                run_dir=run,
                config=run / "rce.toml",
                launch_log=run / "log/launch.log",
                result_path="top",
                publication_required=publication is not None,
            ),
        ),
    )
    assert BatchController(manifest).run() == 0
    if publication:
        publications = tmp_path / "publications.tsv"
        publications.write_text(
            f"id\tstatus\tmessage\n001\t{publication}\tOA publication result\n"
        )
        assert finalize_publications(manifest, publications) == (
            1 if publication == "failed" else 0
        )
    payload = json.loads(manifest.status_json.read_text())
    if publication == "failed":
        assert payload["status"] == "completed_with_errors"
        assert payload["counts"]["failed"] == 1
        assert bool(payload["tasks"][0]["warning_message"]) is (flow == "RCE")
        return
    if flow == "RCE":
        assert payload["status"] == "completed_with_warnings"
        assert payload["counts"]["succeeded_with_warnings"] == 1
        assert "non-signoff" in payload["tasks"][0]["warning_message"]
        assert "succeeded_with_warnings" in manifest.status_tsv.read_text()
    else:
        assert payload["status"] == "completed"
        assert not payload["tasks"][0]["warning_message"]


def test_real_backend_marks_ignored_lvs_batch_as_warning(tmp_path, monkeypatch):
    cfg, paths = make_xrc_config(tmp_path)
    cfg = cfg.replace('lvs', 'ignore_error', value=True)
    calibre = install_fake_calibre(tmp_path / "mgc")
    monkeypatch.setenv("MGC_HOME", str(calibre.parents[1]))
    monkeypatch.setenv("FAKE_CALIBRE_TRACE", str(tmp_path / "trace"))
    monkeypatch.setenv("FAKE_LVS_INCORRECT", "1")
    monkeypatch.setenv("FAKE_LVS_EXIT_CODE", "4")
    root = Path(__file__).resolve().parents[3]
    script = tmp_path / "backend.py"
    script.write_text(
        "\n".join(
            (
                "import sys",
                f"sys.path[:0] = {[str(root / 'rce/python'), str(root / 'common/python')]!r}",
                "from pathlib import Path",
                "from rcepy.config import RceConfig",
                "from rcepy.runner import RceRunner",
                f"cfg = RceConfig(raw={cfg.to_dict()!r}, config_path=Path({str(cfg.config_path)!r}))",
                "sys.exit(RceRunner(cfg).run())",
            )
        )
    )
    run = cfg.run_dir
    manifest = BatchManifest(
        path=tmp_path / "batch.toml",
        schema_version=1,
        batch_id="actual-backend",
        flow="RCE",
        parallel_cells=1,
        temp_dir=tmp_path / ".cad",
        status_json=tmp_path / "status.json",
        status_tsv=tmp_path / "status.tsv",
        tasks=(
            TaskSpec(
                index=1,
                task_id="001",
                label="top",
                command=shlex.join([sys.executable, str(script)]),
                cancel_command="",
                run_dir=run,
                config=cfg.config_path,
                launch_log=run / "log/launch.log",
                result_path=str(paths["output"]),
                publication_required=False,
            ),
        ),
    )
    assert BatchController(manifest).run() == 0
    assert paths["output"].stat().st_size > 0
    payload = json.loads(manifest.status_json.read_text())
    assert payload["status"] == "completed_with_warnings"
    assert payload["tasks"][0]["status"] == "succeeded_with_warnings"
