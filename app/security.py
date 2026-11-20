"""Tokens and a simple rate limiter (standard library only)."""
import hashlib
import secrets
import time
from collections import defaultdict, deque


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_id(nbytes: int = 9) -> str:
    return secrets.token_urlsafe(nbytes)


def new_band_id() -> str:
    # 16 URL-safe chars (~96 bits): not guessable, short enough for the 144-byte tag.
    return secrets.token_urlsafe(12)


class RateLimiter:
    """Allow `limit` events per `window` seconds for each key."""

    def __init__(self, limit: int, window: float) -> None:
        self.limit, self.window = limit, window
        self.events: dict[str, deque] = defaultdict(deque)

    def hit(self, key: str) -> bool:
        now = time.time()
        q = self.events[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True
