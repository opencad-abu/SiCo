"""Prepare project scratch before Qt and the shared LSF input method start."""

from contextlib import contextmanager
import os

from sico_pinyin import input_session
from sicotemp import initialize


@contextmanager
def gui_input(launch_dir):
    previous = dict(os.environ)
    try:
        # Match AI Assistant's terminal entry. In particular, never let Qt use
        # an inherited HOME or site-wide XDG_RUNTIME_DIR as its private runtime.
        try:
            initialize(cwd=launch_dir)
        except (OSError, ValueError) as exc:
            raise ValueError("SiCo GUI environment: " + str(exc)) from None
        with input_session(launch_dir):
            yield
    finally:
        os.environ.clear()
        os.environ.update(previous)
