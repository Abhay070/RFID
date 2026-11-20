"""Password hashing, tokens and a simple rate limiter (standard library only)."""
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    return hmac.compare_digest(digest.hex(), digest_hex)


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
