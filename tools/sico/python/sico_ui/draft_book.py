"""Plain, synchronized draft accounting shared with command completion callbacks."""

import threading
from collections import OrderedDict
from copy import deepcopy

EMPTY_DRAFT = ("", None, 0, 0)


class DraftBook:
    def __init__(self):
        self._values = {}
        self._versions = {}
        self._accepted = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def key(handle, revision):
        return handle.session_id, handle.runtime_id, revision

    def save(self, handle, revision, draft):
        key = self.key(handle, revision)
        with self._lock:
            self._versions[handle.session_id] = key
            self._values[handle.session_id] = EMPTY_DRAFT if key in self._accepted else deepcopy(draft)

    def load(self, handle):
        with self._lock:
            version = self._versions.get(handle.session_id, ())
            if version[:2] == (handle.session_id, handle.runtime_id):
                return deepcopy(self._values.get(handle.session_id, EMPTY_DRAFT))
            return EMPTY_DRAFT

    def accept(self, handle, revision):
        key = self.key(handle, revision)
        with self._lock:
            self._accepted[key] = True
            if self._versions.get(handle.session_id) == key:
                self._values[handle.session_id] = EMPTY_DRAFT
            while len(self._accepted) > 256:
                self._accepted.popitem(last=False)
