"""Task-level model usage summary for one SiCo agent session (M0 baseline).

Reads a private session directory and reports, per task: model rounds, tool
calls, input/output tokens, cached input when the native rollout is present,
the peak single-request input and context occupancy.  It never writes into the
session, never replays an operation and never copies assistant text anywhere.

Frozen counting rules (``sico-session-usage.v1``):

- one *model round* is one model response request; it is counted from the
  ``token.usage`` journal events, which Codex emits once per native turn;
- per-request input is the delta of the cumulative ``input_tokens`` totals, so
  the peak single-request input is available without the native rollout;
- cached input, when reported, is summed from the native rollout's
  ``last_token_usage`` entries after positional alignment; a count mismatch or
  a missing rollout reports ``null`` (not measured), never zero;
- a task's outcome is the terminal ``task.<status>`` journal event; a task
  without one is reported as ``incomplete``.

Usage:

    /software/pkgs/python/3.9.13/bin/python3 tools/utility/mcp_session_usage.py \\
        SESSION_DIR --output /private/evidence/session.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

CONTRACT = "sico-session-usage.v1"
TERMINAL_PREFIX = "task."
# A damaged or runaway journal must not be loaded into memory by accident.
EVENT_LIMIT_BYTES = 256 * 1024 * 1024


class RecordTooLarge(ValueError):
    """The journal exceeds the measurement limit and is not read."""


def _timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def read_events(session: Path, *, limit: int = EVENT_LIMIT_BYTES) -> list[dict[str, Any]]:
    path = session / "events.jsonl"
    size = path.stat().st_size
    if size > limit:
        raise RecordTooLarge(
            f"events.jsonl is {size} B, above the {limit} B measurement limit; "
            "this record needs its own review, not a usage summary")
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _rollout_rounds(session: Path) -> list[dict[str, Any]] | None:
    """Native per-request usage entries in order, or None when unavailable."""
    roots = sorted((session / "codex" / "sessions").rglob("rollout-*.jsonl"))
    if not roots:
        return None
    rounds: list[dict[str, Any]] = []
    for path in roots:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = row.get("payload")
                if row.get("type") != "event_msg" or not isinstance(payload, dict):
                    continue
                if payload.get("type") != "token_count":
                    continue
                info = payload.get("info")
                if isinstance(info, dict):
                    rounds.append(info)
    return rounds


def _task_counter(events: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    tasks: dict[str, dict[str, Any]] = {}
    for event in events:
        task_id = event.get("task_id") or ""
        if not task_id:
            continue
        task = tasks.setdefault(task_id, {
            "task_id": task_id, "text": "", "started": None, "ended": None,
            "outcome": "incomplete", "usage": [], "tools": Counter(),
        })
        kind = event.get("kind", "")
        if kind == "task.started":
            task["started"] = event.get("timestamp")
            payload = event.get("payload") or {}
            task["text"] = payload.get("text", "")
        elif kind.startswith(TERMINAL_PREFIX):
            if task["ended"] is None:
                task["ended"] = event.get("timestamp")
                task["outcome"] = kind[len(TERMINAL_PREFIX):] or "unknown"
        elif kind == "token.usage":
            task["usage"].append(event.get("payload") or {})
        elif kind == "tool.started":
            payload = event.get("payload") or {}
            name = payload.get("name")
            if isinstance(name, str) and name:
                task["tools"][name] += 1
    return tasks


def _rounds_from_usage(task: dict[str, Any]) -> dict[str, Any]:
    cumulative = [row.get("input_tokens", 0) for row in task["usage"]]
    deltas = [total - previous for previous, total in zip([0, *cumulative], cumulative)]
    context = [row.get("context_usage") or {} for row in task["usage"]]
    used = [row.get("used") for row in context if isinstance(row.get("used"), int)]
    last = task["usage"][-1] if task["usage"] else {}
    return {
        "model_rounds": len(cumulative),
        "input_tokens": cumulative[-1] if cumulative else 0,
        "output_tokens": last.get("output_tokens", 0),
        "peak_request_input_tokens": max(deltas) if deltas else 0,
        "context_peak": max(used) if used else None,
        "context_capacity": next(
            (row.get("capacity") for row in reversed(context)
             if isinstance(row.get("capacity"), int)), None),
    }


def summarize(session: Path, *, limit: int = EVENT_LIMIT_BYTES) -> dict[str, Any]:
    events = read_events(session, limit=limit)
    tasks = _task_counter(events)
    rollout = _rollout_rounds(session)
    total_rounds = sum(len(task["usage"]) for task in tasks.values())
    alignment = "matched"
    if rollout is None:
        alignment = "no-rollout"
    elif len(rollout) != total_rounds:
        alignment = "count-mismatch"

    index = 0
    rows = []
    for task in tasks.values():
        entry: dict[str, Any] = {
            "task_id": task["task_id"],
            "text": task["text"],
            "started": task["started"],
            "ended": task["ended"],
            "outcome": task["outcome"],
            "tool_calls": sum(task["tools"].values()),
            "tools": dict(sorted(task["tools"].items())),
            **_rounds_from_usage(task),
            "cached_input_tokens": None,
            "cache_write_input_tokens": None,
        }
        started, ended = _timestamp(task["started"] or ""), _timestamp(task["ended"] or "")
        entry["duration_seconds"] = (
            round((ended - started).total_seconds(), 3)
            if started is not None and ended is not None else None)
        rounds = len(task["usage"])
        if alignment == "matched":
            window = rollout[index:index + rounds]
            entry["cached_input_tokens"] = sum(
                (info.get("last_token_usage") or {}).get("cached_input_tokens", 0)
                for info in window)
            entry["cache_write_input_tokens"] = sum(
                (info.get("last_token_usage") or {}).get("cache_write_input_tokens", 0)
                for info in window)
        index += rounds
        rows.append(entry)

    if alignment == "matched":
        cached: int | None = sum(entry["cached_input_tokens"] for entry in rows)
        cache_write: int | None = sum(entry["cache_write_input_tokens"] for entry in rows)
    else:
        cached = cache_write = None
    return {
        "contract": CONTRACT,
        "session": {
            "session_id": session.name,
            "path": str(session),
            "tasks": len(rows),
            "model_rounds": total_rounds,
            "tool_calls": sum(entry["tool_calls"] for entry in rows),
            "input_tokens": sum(entry["input_tokens"] for entry in rows),
            "output_tokens": sum(entry["output_tokens"] for entry in rows),
            "peak_request_input_tokens": max(
                (entry["peak_request_input_tokens"] for entry in rows), default=0),
            "cached_input_tokens": cached,
            "cache_write_input_tokens": cache_write,
            "outcomes": dict(sorted(Counter(
                entry["outcome"] for entry in rows).items())),
        },
        "rollout": {
            "alignment": alignment,
            "token_count_events": None if rollout is None else len(rollout),
            "journal_rounds": total_rounds,
            "note": (
                "positional alignment of the native token_count stream with the "
                "journal token.usage stream; null means not measured"),
        },
        "tasks": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("session", type=Path, help="session directory (private record)")
    parser.add_argument("--output", type=Path,
                        help="write the summary JSON here; default prints to stdout")
    arguments = parser.parse_args(argv)

    summary = summarize(arguments.session.resolve())
    if arguments.output is not None:
        arguments.output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        session = summary["session"]
        print(f"{session['session_id']}: {session['tasks']} tasks, "
              f"{session['model_rounds']} rounds, {session['tool_calls']} tool calls, "
              f"input {session['input_tokens']}, peak request "
              f"{session['peak_request_input_tokens']}, cached "
              f"{session['cached_input_tokens']} ({summary['rollout']['alignment']})")
        for entry in summary["tasks"]:
            print(f"  {entry['task_id'][:8]} {entry['outcome']:>16} "
                  f"rounds {entry['model_rounds']:>3} calls {entry['tool_calls']:>3} "
                  f"in {entry['input_tokens']:>9} peak {entry['peak_request_input_tokens']:>7} "
                  f"{entry['text'][:30]}")
        print(f"written to {arguments.output}")
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
