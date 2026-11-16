"""Stage-plan ID compatibility and bounded published task-tree tests."""

from sico.storage.journal import SessionJournal

from test_agent_task_tree import backend_for, finish, plan, start_task


def test_stage_plan_accepts_new_ids_and_rejects_foreign_ids(tmp_path):
    with SessionJournal(tmp_path, "stage-plan-ids") as journal:
        backend = backend_for(journal)
        start_task(backend, "设计一个 LDO", "input-1")
        accepted = plan(backend, "LDO 设计", [
            {"stage_id": "window_inspection", "stage_title": "规格确认",
             "objective": "确认规格", "acceptance": "规格表"},
            {"stage_id": "summary", "stage_title": "汇总结果",
             "objective": "汇总", "acceptance": "报告"},
        ]).data
        assert [row["id"] for row in accepted["plan"]["stages"]] == [
            "window_inspection", "summary"]
        finish(backend)
        start_task(backend, "设计一个比较器", "input-2")
        other = plan(backend, "比较器设计", [
            {"stage_title": "输入检查", "objective": "检查", "acceptance": "记录"},
        ]).data
        foreign_id = other["plan"]["stages"][0]["id"]
        rejected = plan(backend, "LDO 设计", [
            {"stage_id": foreign_id, "stage_title": "复用外部阶段",
             "objective": "检查", "acceptance": "记录"},
        ], work_id=accepted["work"]["id"])
        assert rejected.status == "tool_error"
        assert "another work" in rejected.summary


def test_published_task_tree_keeps_bounded_children():
    from sico.service.state_publication import _bounded_tree

    tree = {
        "works": [{
            "id": "w1", "title": "工程", "status": "active",
            "stages": [{
                "id": "s1", "title": "阶段", "status": "active",
                "executions": [{
                    "id": "input-1", "task_id": "t1", "text": "任务",
                    "status": "completed", "origin": "chat", "diagnostic": "",
                    "children": [{
                        "thread_id": "c%02d" % index,
                        "parent_thread_id": "thread",
                        "name": "子任务 %02d" % index,
                        "status": "completed", "tool": "subAgentActivity",
                        "updated_at": "2026-10-19T00:00:00Z",
                    } for index in range(25)],
                }],
            }],
        }],
        "unassigned": [], "truncated": False,
    }
    published = _bounded_tree(tree)
    children = published["works"][0]["stages"][0]["executions"][0]["children"]
    assert len(children) == 20
    assert children[0]["thread_id"] == "c00"
