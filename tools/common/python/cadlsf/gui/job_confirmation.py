"""Explicit confirmation dialog for an LSF job termination."""

from cadgui.prompts import CANCEL, SiConfirm
from ..model import JobInfo


def confirm_job_kill(job: JobInfo, parent=None) -> bool:
    detail = f"Kill LSF job {job.job_id}?"
    if job.name:
        detail += f"\n\nJob name: {job.name}"
    detail += "\n\nThis action cannot be undone."
    dialog = SiConfirm("Kill LSF Job", detail, parent)
    dialog.setObjectName("killJobDialog")
    dialog.add_choice("kill", "Kill Job", danger=True)
    dialog.add_choice(CANCEL, CANCEL, default=True, escape=True)
    return dialog.ask() == "kill"
