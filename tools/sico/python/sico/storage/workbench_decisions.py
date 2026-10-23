"""Decision evidence rows for answered audits."""


def reply_content(row):
    reply = row["reply"]
    if reply.get("automatic"):
        return {
            "audit_id": row["id"], "回答者": reply["actor"],
            "答复": "未等待用户；模型按公开建议自行决定",
            "交付": "已发送；服务结果另行核对",
        }
    if "elicitation" in row:
        return {"audit_id": row["id"], "回答者": reply["actor"],
                "答复": reply["response"],
                "交付": "已发送；服务结果另行核对"}
    return {
        "audit_id": row["id"], "回答者": reply["actor"],
        "答复": [{"问题": q["question"],
                  "选择": reply["answers"][q["id"]]["choice"],
                  "补充": reply["answers"][q["id"]]["text"]}
                 for q in row["questions"]],
    }


def record_decision(index, event, row):
    automatic = row["reply"].get("automatic") is True
    row["reply_data"] = index._data(
        event, "decision",
        row["title"] + (" · 模型决定" if automatic else " · 用户决定"),
        "process", reply_content(row),
        data_type="decision", audit_ids=[row["id"]],
        parents=[r["id"] for r in row["evidence"]],
        work_id=row["work_id"], stage_id=row["stage_id"],
    )
