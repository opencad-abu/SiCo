"""Qt renderer over the shared, Qt-independent chat projection."""

from PyQt5.QtGui import QTextCursor

from sico.service.chat_projection import ChatProjection
from sico.service.chat_projection import system_user_name as system_user_name

from .presentation import event_time, recommended_label
from .transcript_render import (
    COUNTER_ICON as COUNTER_ICON,
)
from .transcript_render import (
    TRIANGLE_COLLAPSED as TRIANGLE_COLLAPSED,
)
from .transcript_render import (
    TRIANGLE_EXPANDED as TRIANGLE_EXPANDED,
)
from .transcript_render import (
    SafeDocument as SafeDocument,
)
from .transcript_render import (
    TranscriptRenderer,
)


class Transcript(ChatProjection, TranscriptRenderer):
    RENDER_MESSAGES = 200
    RENDER_CHARS = 65536

    def __init__(self, username=None, *, read_only=False):
        super().__init__(username, read_only=read_only)
        self._document = None
        self._rendered = []
        self._positions = []
        self._last_render = float('-inf')

    def scroll_to_message(self, view, message_id):
        """Move the visible cursor to a rendered message; no history inference."""
        for index, signature in enumerate(self._rendered):
            if str(signature[0]) == str(message_id):
                cursor = QTextCursor(view.document())
                cursor.setPosition(self._positions[index])
                view.setTextCursor(cursor)
                view.ensureCursorVisible()
                return True
        return False

    # Preserve per-instance presentation hooks used by older callers.
    @staticmethod
    def _recommended_label(value):
        return recommended_label(value)

    @staticmethod
    def _event_time(value):
        return event_time(value)
