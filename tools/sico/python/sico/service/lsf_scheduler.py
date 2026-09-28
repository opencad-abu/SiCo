"""Submit/query/cancel LSF jobs by captured cluster, owner and stable job name."""

import re
import shlex

from ..transport.framing import ProtocolError
from .lsf_commands import command_environment, run_command
from .lsf_output import FIELDS, history, jobs, missing, submitted


class LsfScheduler:
    def __init__(self, config, *, environment=None, runner=run_command):
        self.config, self.runner = config, runner
        self.environment = command_environment(environment)
        self._mode = None

    def _run(self, executable, *args):
        return self.runner([executable, *args], environment=self.environment,
                           timeout=self.config.command_timeout)

    def verify_cluster(self):
        code, raw = self._run(self.config.lsid)
        match = re.search(r"^My cluster name is (\S+)\s*$", raw, re.M)
        if code or match is None or match[1] != self.config.cluster:
            raise ProtocolError("Captured LSF cluster is unavailable or changed")
        return match[1]

    def probe(self):
        self.verify_cluster()
        code, raw = self._run(self.config.bjobs, "-json", "-a", "-u", self.config.owner,
                              "-J", "sico-probe-no-job", "-o", "jobid user stat job_name")
        if "illegal option" in raw or "unrecognized option" in raw:
            self._mode = "fields"
        elif missing(raw, 'sico-probe-no-job'):
            self._mode = 'json'
        elif code == 0:
            jobs(raw, "json")
            self._mode = "json"
        else:
            raise ProtocolError("LSF status capability probe failed")
        return self._mode

    def submit(self, name, manifest):
        self.verify_cluster()
        c = self.config
        argv = ["-q", c.queue, "-J", name, "-n", str(c.slots), "-W", str(c.wall_minutes),
                "-cwd", str(manifest.parent), "-oo", str(manifest.parent / "scheduler.out"),
                "-eo", str(manifest.parent / "scheduler.err")]
        if c.resource:
            argv += ["-R", c.resource]
        code, raw = self._run(c.bsub, *argv,
            shlex.join([*c.node_command, "background-node", "--connection-file", str(manifest)]))
        if code:
            raise RuntimeError("LSF submit result unconfirmed")
        return submitted(raw)

    def observe(self, expected):
        if (expected.get('cluster') != self.config.cluster
                or expected.get('owner') != self.config.owner):
            raise ProtocolError('Scheduler observation belongs to another cluster or owner')
        self.verify_cluster()
        if self._mode is None:
            self.probe()
        args = ["-a", "-u", expected["owner"], "-J", expected["name"]]
        args += (["-json", "-o", "jobid user stat job_name"] if self._mode == "json"
                 else ["-noheader", "-o", FIELDS])
        code, raw = self._run(self.config.bjobs, *args)
        absent = missing(raw, expected['name'])
        if code and not absent:
            raise OSError('LSF query unavailable')
        rows = [] if absent else jobs(raw, self._mode)
        matches = [r for r in rows if r["name"] == expected["name"]
                   and r["owner"] == expected["owner"]]
        if len(matches) > 1:
            raise ProtocolError("LSF submission identity is ambiguous")
        if matches:
            row = matches[0]
            if expected.get("job_id") and row["job_id"] != expected["job_id"]:
                raise ProtocolError("LSF job ID changed or was reused")
            return dict(row, cluster=self.config.cluster)
        if expected.get("job_id"):
            code, raw = self._run(self.config.bhist, "-l", expected["job_id"])
            if code == 0 and raw.strip() and not missing(raw, expected['job_id']):
                return history(raw, expected)
        return dict(expected, state="UNKWN")

    def cancel(self, expected):
        observed = self.observe(expected)
        if observed["state"] in {"DONE", "EXIT"}:
            return "terminal_observed"
        if not observed.get("job_id") or observed["state"] == "UNKWN":
            return "unconfirmed"
        # Name filtering is evaluated by LSF at cancellation time too; a recycled
        # numeric ID must not select another job after the preceding observation.
        code, _raw = self._run(self.config.bkill, '-J', expected['name'], '0')
        return "requested" if code == 0 else "unconfirmed"
