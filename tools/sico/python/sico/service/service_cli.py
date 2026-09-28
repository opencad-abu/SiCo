"""Command-line presentation of service lifecycle requests, outside Qt."""

import json
from dataclasses import asdict

from .service_admin import ServiceAdmin


def run_admin(args):
    if getattr(args, "force", False):
        return run_force_stop(args.project_dir)
    request = ServiceAdmin(args.project_dir, timeout=args.timeout)
    try:
        result = request.ready.result(args.timeout)
        if args.command == "service-stop" and result["service"] is not None:
            request = ServiceAdmin(args.project_dir, stop=result["service"], timeout=args.timeout)
            result = request.ready.result(args.timeout)
    except Exception as exc:
        print(json.dumps(dict(state="unknown", error=type(exc).__name__)))
        return 1
    finally:
        request.close()
    if result["service"] is not None:
        result["service"] = asdict(result["service"])
    print(json.dumps(result, ensure_ascii=False))
    if args.command == "service-stop":
        return 0 if result["state"] in {"stopping", "absent", "inactive"} else 1
    return 0


def run_force_stop(project):
    from .service_discovery import discover_project
    from .service_force_stop import WAIT_SECONDS, ForceServiceStop

    request = None
    try:
        found = discover_project(project)
        if found.state in {"absent", "inactive"}:
            print(json.dumps(dict(state=found.state), ensure_ascii=False))
            return 0
        if found.state != "local_candidate":
            raise ValueError("无法确认本机服务身份，未发送终止信号。")
        request = ForceServiceStop(project, found.service)
        result = request.ready.result(WAIT_SECONDS + 1)
        print(json.dumps(dict(state="stopped", **asdict(result)), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps(dict(state="unknown", error=str(exc)), ensure_ascii=False))
        return 1
    finally:
        if request is not None:
            request.close()
