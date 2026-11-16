"""Offline event histories for bounded workbench query acceptance."""

from types import SimpleNamespace

from sico.core.contracts import BoundContext
from sico.storage.workbench import content_digest


class History:
    def __init__(self, session_id="query"):
        self.session_id = session_id
        self.context = BoundContext("i", "g", "target", {})
        self.events, self.tools, self.reports = [], [], []
        self.reader = SimpleNamespace(session_id=session_id, stream_events=self.stream_events)

    def stream_events(self, after):
        return iter(self.events[after:])

    def add(self, kind, payload, task="task_0"):
        row = {"session_id": self.session_id, "task_id": task,
               "sequence": len(self.events) + 1, "timestamp": "2026-09-17T00:00:00+00:00",
               "kind": kind, "payload": payload}
        self.events.append(row)
        return row

    def populate(self, tasks=4, tools=30, reports=4):
        for t in range(tasks):
            task, work, stage = f"task_{t}", f"work_{t % 2}", f"stage_{t}"
            self.add("task.started", {"text": "Query task " + str(t),
                                     "context": self.context.record()}, task)
            self.add("workbench.stage.selected", {
                "work": {"id": work, "title": "Work " + str(t % 2)},
                "stage": {"id": stage, "work_id": work, "stage_title": "Stage " + str(t)},
            }, task)
            for n in range(tools):
                name = ("read_alpha", "measure_beta", "execute_circuit_operation")[n % 3]
                result = {"status": "ok" if n % 2 else "failed", "data": {"payload": "x" * 1024}}
                self.add("tool.started", {"id": str(n), "name": name, "input": {}}, task)
                event = self.add("tool.finished", {"id": str(n), "name": name,
                                                   "result": result}, task)
                key = f"d_{event['sequence']}_tool"
                self.tools.append({"id": key, "task_id": task, "sequence": event["sequence"],
                                   "name": name, "digest": content_digest({"value": result,
                                                                            "inputs": {}})})
            for n in range(reports):
                evidence = self.tools[-1]
                report = {"id": f"report_{t}_{n}", "version": 1, "task_id": task,
                          "work_id": work, "stage_id": stage, "title": f"Report {t}/{n:04}",
                          "outcome": "completed", "markdown": "Verified body", "next_steps": "",
                          "evidence": [{"id": evidence["id"], "digest": evidence["digest"]}]}
                self.add("workbench.report.published", report, task)
                self.reports.append(report)
            self.add("model.completed", {"text": "Final answer"}, task)
            self.add("task.completed", {}, task)
        return self
