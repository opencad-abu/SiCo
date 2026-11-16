"""Service queries retain pagination, history scope, attachment and detail integrity."""

import base64
import threading
from dataclasses import replace

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service, open_session
from agent_service_gui_fixtures import drain
from agent_workbench_query_fixtures import History
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QLabel

from sico.core.contracts import RunState
from sico.service.frontend_assets import input_value
from sico.service.remote_frontend import RemoteFrontend
from sico.service.workbench_query import PageQuery, WorkbenchQuery
from sico.storage.input_assets import archive
from sico.storage.journal import SessionJournal
from sico.transport.framing import ProtocolError


def test_history_pagination_search_sort_and_scoped_detail(tmp_path, monkeypatch):
    history = History("history").populate(tasks=3, tools=130, reports=3)
    with SessionJournal(tmp_path, "history") as journal:
        for row in history.events:
            state = RunState(history.context, task=dict(id=row["task_id"], status="executing"))
            journal.append(row["kind"], row["payload"], state)
    with business_service(tmp_path, monkeypatch) as service:
        open_session(service)
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        try:
            api.open_initial("attach", dict(session_id="live")).result(5)
            prepared = api.preview_session("history", 3, 200).result(5)
            source = api.preview_source(prepared)
            query = WorkbenchQuery(activation=7, query_id="search", data=PageQuery(
                keyword="measure_beta", sort="sequence_asc", page_size=40))
            version, first = source.query_pages(query).result(5)
            assert version == first.version and len(first.data) == 40
            assert first.pages["data"]["total"] == 129
            assert [r["sequence"] for r in first.data] == sorted(r["sequence"] for r in first.data)
            changed = replace(query, query_id="page2", data=replace(query.data, offset=40))
            _, second = source.query_pages(changed).result(5)
            assert not {r["id"] for r in first.data} & {r["id"] for r in second.data}
            detail = source.detail("data", first.data[0]["id"]).result(5)
            assert "measure_beta" in str(detail)
            assert len(first.report_list()) == 9
            _, normalized = source.query_pages(replace(query, work_id="deleted-work")).result(5)
            assert normalized.query.work_id == ""
            with pytest.raises(ValueError):
                source.detail("data", "not-a-published-key").result(5)
            service.dispatch.frontend.workspace.catalog.request().result(5)
            catalog = api._queries.request("catalog", dict(version=None)).result(5)["catalog"]
            assert {"history", "live"} <= {row["id"] for row in catalog["rows"]}
            assert "history" not in service.owner.controllers
        finally:
            api.close_desktop()


def test_preview_without_message_limit_uses_whole_committed_history(tmp_path, monkeypatch):
    from agent_replay_fixtures import record_task

    with business_service(tmp_path, monkeypatch) as service:
        token = open_session(service).session
        controller = service.owner.controllers["live"]
        record_task(controller.journal, controller.current, "old")
        record_task(controller.journal, controller.current, "recent")
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        try:
            api.open_initial("attach", dict(session_id="live")).result(5)
            prepared = api.preview_session(token.session_id, 5, 0).result(5)
            batches = drain(api, prepared.stream)
            tasks = [e["task_id"] for b in batches for e in b.events if e["kind"] == "task.started"]
            assert tasks == ["old", "recent"]
        finally:
            api.close_desktop()


def test_large_asset_and_event_detail_are_verified_in_background(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    with business_service(tmp_path, monkeypatch) as service:
        token = open_session(service).session
        controller = service.owner.controllers["live"]
        # Base64 result exceeds the normal 4 MiB transfer budget.
        data = b"verified input " * 230000
        asset = archive(controller.journal, data, ".bin")
        state = RunState(controller.current, task=dict(id="input", status="executing"))
        started = controller.journal.append("task.started", dict(
            text="input evidence", context=controller.current.record(),
            inputs=[dict(type="mention", name="evidence", asset=asset)]), state)
        controller.journal.append("model.completed", dict(text="large " * 3000), state)
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        widget = QLabel("asset read")
        try:
            api.open_initial("attach", dict(session_id="live")).result(5)
            prepared = api.prepare_replay(token, 1, 200).result(5)
            batches = drain(api, prepared.stream)
            event = next(e for b in batches for e in b.events if "detail" in e)
            detail = api.event_detail(prepared.stream, event["detail"], 4096).result(5)
            assert detail["offset"] == 4096 and len(detail["text"]) <= 4096
            import sico.service.input_details as module

            original = module.read_asset
            def slow(*args):
                entered.set()
                assert release.wait(5)
                return original(*args)

            monkeypatch.setattr(module, "read_asset", slow)
            receipt = api.input_detail(prepared.stream, started["sequence"], 0)
            wait_until(entered.is_set)
            heartbeat(widget)
            assert not release.is_set() and not receipt.done()
            release.set()
            result = receipt.result(20)
            assert result["data"] == data and result["input"]["asset"] == asset
            with pytest.raises(ProtocolError):
                input_value(dict(data=dict(size=1, base64="YQ==", sha256="f" * 64)))
            (controller.journal.root / asset["path"]).write_bytes(b"damaged")
            with pytest.raises(ValueError, match="checksum"):
                api.input_detail(prepared.stream, started["sequence"], 0).result(5)
        finally:
            release.set()
            api.close_desktop()
            widget.close()


def test_failed_source_construction_and_stale_runtime_close_release_subscriptions(
        tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service:
        token = open_session(service).session
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        replay = service.dispatch.frontend.replay
        try:
            api.open_initial("attach", dict(session_id="live")).result(5)
            with monkeypatch.context() as patch:
                def full(*args):
                    raise ValueError("source capacity exhausted")
                patch.setattr(replay.workspace.data, "source", full)
                with pytest.raises(ValueError, match="capacity"):
                    api.prepare_replay(token, 1, 200).result(5)
            wait_until(lambda: not replay.workspace.events._streams)
            assert not replay._feeds
            prepared = api.prepare_replay(token, 2, 200).result(5)
            controller = service.owner.controllers["live"]
            controller.runtime_id = "f" * 32
            with pytest.raises(ValueError):
                api.event_batch(prepared.stream).result(5)
            prepared.stream.close()
            wait_until(lambda: not replay._feeds)
        finally:
            api.close_desktop()


def test_native_image_detail_round_trips_through_history_query(tmp_path, monkeypatch):
    from test_codex_native_projection import begin, emit

    data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/"
        "axZ1V8AAAAASUVORK5CYII=")
    with SessionJournal(tmp_path, "image-history") as journal:
        backend = begin(journal)
        item = dict(id="image", type="imageGeneration", status="completed",
                    result="data:image/png;base64," + base64.b64encode(data).decode("ascii"))
        emit(backend, "item/started", item=item)
        emit(backend, "item/completed", item=item)
    with business_service(tmp_path, monkeypatch) as service:
        api = RemoteFrontend(service.descriptor, tmp_path, "live")
        try:
            prepared = api.preview_session("image-history", 1).result(5)
            source = api.preview_source(prepared)
            _, page = source.query_pages(WorkbenchQuery(activation=1)).result(5)
            row = next(row for row in page.data if row["category"] == "process"
                       and row["status"] == "ok")
            detail = source.detail("data", row["id"]).result(5)
            assert not detail["notice"] and detail["images"] == [data]
            assert not service.owner.controllers
            with pytest.raises(TypeError):
                detail["images"].append(b"replacement")
        finally:
            api.close_desktop()
