"""Submit captured background-node jobs using shared LSF job control."""

import shlex

from ..lsf.job_control import LsfJobControl
from ..lsf.output import submitted


class LsfScheduler(LsfJobControl):
    """Own only background-node submission; shared control owns observation state."""

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
