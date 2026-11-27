"""Passwords and session tokens (backlog 0.4): argon2 hashes checked off the event loop,
the count of failed logins of one client address (backlog S9), temporary passwords an
admin or a specialist hands over, and the token of a login session, of which only the
SHA-256 is stored.
"""

import asyncio
import hashlib
import secrets
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128
# Password hashes computed at once (audit N6). Each takes 64 MiB and about a quarter of a
# CPU-second: two keep a small instance's processor busy, and the rest wait their turn in
# a queue. The threads are their own, so a crowd of logins does not take the shared pool
# that file storage, mail and workbooks run in.
PASSWORD_CHECKS_AT_ONCE = 2
_password_threads = ThreadPoolExecutor(PASSWORD_CHECKS_AT_ONCE, thread_name_prefix="password")
# Clients whose failed logins are remembered: beyond it the oldest are forgotten, so the
# memory stays small however many addresses come.
MAX_CLIENTS_REMEMBERED = 10_000
# A clock that only goes forward (tests move it).
_monotonic = time.monotonic

_hasher = PasswordHasher()
# Verified against when the email is unknown, so a miss takes as long as a wrong password.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))
# No 0/O, 1/l/I: temporary passwords are read off a screen and typed by hand.
_TEMP_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        _hasher.verify(password_hash or _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False
    return password_hash is not None


async def check_password(password_hash: str | None, password: str) -> bool:
    """verify_password in the password threads: off the event loop, which every other
    request waits on, and no more than PASSWORD_CHECKS_AT_ONCE at a time. Where anyone may
    ask (the login), call it with no transaction open: the queue and the check would hold
    a database connection all along."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_password_threads, verify_password, password_hash, password)


async def make_password_hash(password: str) -> str:
    """hash_password in the password threads, like check_password."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_password_threads, hash_password, password)


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


class ClientFailures:
    """Failed logins of each client over a sliding window (audit N6, h01.7): a client with
    `limit` of them waits until the oldest is `window` seconds old.

    A try counts when it starts, so tries sent at once cannot outrun the limit, and one
    that turns out right is given back. Kept in memory: the site runs as one process, and
    a restart forgets the counts."""

    def __init__(self, limit: int, window: float) -> None:
        self.limit = limit
        self.window = window
        self._tries: dict[str, deque[float]] = {}

    def take(self, client: str) -> float | None:
        """Count a try and return its mark for give_back; None, counting nothing, when the
        client has used up the window."""
        now = _monotonic()
        tries = self._tries.get(client)
        if tries is None:
            self._make_room(now)
            tries = self._tries[client] = deque()
        while tries and tries[0] <= now - self.window:
            tries.popleft()
        if len(tries) >= self.limit:
            return None
        tries.append(now)
        return now

    def give_back(self, client: str, mark: float) -> None:
        """The try was not a failure."""
        tries = self._tries.get(client)
        if tries is not None and mark in tries:
            tries.remove(mark)

    def _make_room(self, now: float) -> None:
        if len(self._tries) < MAX_CLIENTS_REMEMBERED:
            return
        stale = [
            c for c, tries in self._tries.items() if not tries or tries[-1] <= now - self.window
        ]
        for client in stale:
            del self._tries[client]
        while len(self._tries) >= MAX_CLIENTS_REMEMBERED:
            del self._tries[next(iter(self._tries))]


def generate_temporary_password(length: int = 14) -> str:
    return "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(length))


def new_session_token() -> tuple[str, str]:
    """Returns (token for the cookie, hash to store)."""
    token = secrets.token_urlsafe(32)
    return token, hash_session_token(token)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
