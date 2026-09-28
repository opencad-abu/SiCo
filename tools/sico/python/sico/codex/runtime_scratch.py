"""One native runtime's scratch directory, separate from retained Codex history."""

import os
import shutil
import stat
import tempfile
from pathlib import Path

from ..storage.journal import private_dir


class RuntimeScratch:
    def __init__(self, home):
        root = home / "tmp"
        private_dir(root)
        self.path = Path(tempfile.mkdtemp(prefix="runtime-", dir=str(root)))
        self._identity = self.path.stat()
        self._closed = False

    def close(self):
        if self._closed:
            return
        info = self.path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or (info.st_dev, info.st_ino) !=
                (self._identity.st_dev, self._identity.st_ino)):
            raise ValueError("Native scratch identity changed; retained for reconciliation")
        shutil.rmtree(self.path)
        self._closed = True
