import time


class Denylist:
    def __init__(self, token_ttl_seconds: int) -> None:
        self._token_ttl_seconds = token_ttl_seconds
        self._revoked_at: dict[str, float] = {}

    def revoke_user(self, user_id: int, revoked_at: float | None = None) -> None:
        self._revoked_at[str(user_id)] = revoked_at if revoked_at is not None else time.time()

    def is_revoked(self, user_id: str, issued_at: int, now: float | None = None) -> bool:
        current_time = now if now is not None else time.time()
        self._prune(current_time)
        revoked_at = self._revoked_at.get(user_id)
        return revoked_at is not None and issued_at < revoked_at

    def _prune(self, now: float) -> None:
        expired_before = now - self._token_ttl_seconds
        self._revoked_at = {
            user_id: revoked_at
            for user_id, revoked_at in self._revoked_at.items()
            if revoked_at >= expired_before
        }
