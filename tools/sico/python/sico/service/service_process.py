"""Spawn a detached service with a private one-shot bootstrap channel."""

import os
import socket
import subprocess
import threading

from ..interpreter import agent_command


def spawn_service(project):
    parent, child_channel = socket.socketpair()
    try:
        command = agent_command("project-service", "--project-dir", str(project),
                                "--bootstrap-fd", str(child_channel.fileno()))
        environment = dict(os.environ)
        for key in ("PYTHONHOME", "PYTHONPATH", "LD_PRELOAD", "LD_AUDIT"):
            environment.pop(key, None)
        environment["PYTHONNOUSERSITE"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process = subprocess.Popen(
            command, cwd=str(project), env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
            pass_fds=(child_channel.fileno(),), start_new_session=True,
        )
        # Reap even when the waiter detaches. Daemon threads do not retain a GUI.
        threading.Thread(target=process.wait, name="service-reaper", daemon=True).start()
        return parent
    except BaseException:
        parent.close()
        raise
    finally:
        child_channel.close()
