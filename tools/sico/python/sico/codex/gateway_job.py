"""One interactive AI-queue job with scheduler identity and explicit cleanup evidence."""

import pwd
import json
import re
import shlex
import shutil
import time
import uuid
from pathlib import Path

from ..interpreter import cad_python, agent_command
from ..service.lsf_commands import command_environment, run_command
from ..service.lsf_config import LsfConfig
from ..service.lsf_scheduler import LsfScheduler
from ..storage.journal import open_private
from .local_process import LocalProcess
import os


def launcher(environment):
    configured = environment.get("SICO_CODEX_LSF_EXE", "").strip()
    if not configured:
        return None
    executable = shutil.which(configured, path=environment.get("PATH"))
    if not executable:
        raise ValueError("SICO_CODEX_LSF_EXE is not executable")
    args = shlex.split(environment.get("SICO_CODEX_LSF_ARGS", "-Is -q ai"))
    if not args or args[0] not in {"-I", "-Is"}:
        raise ValueError("SICO_CODEX_LSF_ARGS must start with -I or -Is")
    options = {}
    for i in range(1, len(args), 2):
        if i + 1 >= len(args) or args[i] not in {"-q", "-m", "-R", "-n", "-W", "-app"}:
            raise ValueError("Unsupported SICO_CODEX_LSF_ARGS option")
        if args[i] in options or any(c in args[i + 1] for c in "\0\r\n"):
            raise ValueError("Invalid SICO_CODEX_LSF_ARGS")
        options[args[i]] = args[i + 1]
    if not options.get("-q"):
        raise ValueError("SICO_CODEX_LSF_ARGS requires an explicit queue")
    return str(Path(executable).absolute()), args, options


class GatewayJob:
    def __init__(self, environment, cwd, evidence):
        executable, args, options = launcher(environment)
        self.process = None
        self.path = Path(evidence) / (uuid.uuid4().hex + ".json")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tools = {
            name: str(Path(executable).parent / name)
            for name in ("bjobs", "bkill", "bhist", "lsid")
        }
        scheduler_env = command_environment(environment)
        code, raw = run_command([tools["lsid"]], environment=scheduler_env, timeout=5)
        cluster = re.search(r"^My cluster name is (\S+)\s*$", raw, re.M)
        if code or cluster is None:
            raise ValueError("LSF gateway cluster is unavailable")
        config = LsfConfig(
            cluster=cluster[1],
            owner=pwd.getpwuid(os.getuid()).pw_name,
            queue=options["-q"],
            shared_root=str(cwd),
            node_command=(cad_python(environment),),
            bsub=executable,
            **tools,
        )
        self.scheduler = LsfScheduler(config, environment=scheduler_env)
        self.expected = dict(
            cluster=config.cluster, owner=config.owner, name="sico-ai-" + self.path.stem
        )
        self.record = dict(scheduler=self.expected, phase="submission_intent")
        self.save()
        worker = agent_command("gateway-worker")
        if worker[0].endswith("python3") or "-s" in worker:
            worker[0] = cad_python(environment)
        env = dict(environment, BSUB_QUIET="1", PYTHONNOUSERSITE="1")
        # Model secrets travel only after the private worker handshake, never in
        # bsub argv, inherited model variables, scheduler logs or evidence files.
        for name in list(env):
            if any(
                word in name.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")
            ):
                env.pop(name)
        try:
            self.process = LocalProcess(
                [executable, *args, "-J", self.expected["name"], shlex.join(worker)],
                env,
                cwd,
            )
        except BaseException:
            self.close()
            raise

    def save(self):
        with os.fdopen(
            open_private(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC), "w"
        ) as out:
            json.dump(self.record, out)

    def close(self):
        if self.record.get("phase") == "closed":
            return
        local_error = None
        if self.process:
            try:
                self.process.close()
                self.process.child.stdout.close()
            except (OSError, RuntimeError) as exc:
                local_error = exc
        deadline = time.monotonic() + 8
        try:
            observed = self.scheduler.observe(self.expected)
            if observed.get("job_id"):
                self.expected.update(job_id=observed["job_id"])
            if observed["state"] not in {"DONE", "EXIT"}:
                self.scheduler.cancel(self.expected)
            while (
                observed["state"] not in {"DONE", "EXIT"}
                and time.monotonic() < deadline
            ):
                time.sleep(0.1)
                observed = self.scheduler.observe(self.expected)
            self.record.update(
                phase="closed"
                if observed["state"] in {"DONE", "EXIT"}
                else "cleanup_unconfirmed",
                observation=observed,
            )
        except (OSError, ValueError, RuntimeError, TimeoutError):
            self.record["phase"] = "cleanup_unconfirmed"
        if local_error is not None:
            self.record["phase"] = "cleanup_unconfirmed"
        self.save()
        if self.record["phase"] != "closed":
            raise RuntimeError(
                "AI queue job cleanup unconfirmed; inspect codex/lsf-gateway evidence"
            )
