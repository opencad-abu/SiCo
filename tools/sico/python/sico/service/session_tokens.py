"""Backend authority for resolving session identities."""

from .frontend_session import SessionToken


class SessionTokens:
    def __init__(self, sessions):
        self._sessions = sessions

    @staticmethod
    def check(token):
        if type(token) is not SessionToken:
            raise TypeError("A SessionToken is required")
        if not isinstance(token.session_id, str) or not isinstance(token.runtime_id, str):
            raise TypeError("Session identity must contain strings")
        return token

    def current(self, token):
        self.check(token)
        owner = self._sessions.controllers.get(token.session_id)
        return owner is not None and getattr(owner, "runtime_id", None) == token.runtime_id

    def resolve(self, token):
        self.check(token)
        owner = self._sessions.controllers.get(token.session_id)
        if owner is None or getattr(owner, "runtime_id", None) != token.runtime_id:
            raise ValueError("Session runtime is no longer current")
        return owner

    def session(self, session_id):
        owner = self._sessions.controllers.get(session_id)
        return self.internal_token(owner) if owner is not None else None

    def internal_token(self, value):
        """Legacy backend controller input; remove when backend callers use tokens.

        FrontendPort never invokes this compatibility branch with UI input.
        Only the exact registered controller can enter it.
        """
        if type(value) is SessionToken:
            self.resolve(value)
            return value
        from .controller import SessionController

        if not isinstance(value, SessionController):
            raise TypeError("Expected a registered backend controller")
        if self._sessions.controllers.get(value.session_id) is not value:
            raise ValueError("Session runtime is no longer current")
        return SessionToken(value.session_id, value.runtime_id)
