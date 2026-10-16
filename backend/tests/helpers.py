import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pyotp
from httpx import AsyncClient, Response

PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "staple-and-a-pony-42"
# Test-only TOTP secret of users created by the make_user fixture.
TOTP_SECRET = "JBSWY3DPEHPK3PXP"

MakeUser = Callable[..., Awaitable[uuid.UUID | None]]


def workspace_today() -> date:
    """Today as the app counts it: in the specialist's time zone (Europe/Minsk by default),
    not in UTC — the two differ for three hours every night."""
    return datetime.now(ZoneInfo("Europe/Minsk")).date()


class FakeClock:
    """Time for TOTP checks in tests (patched into app.core.two_factor)."""

    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def time(self) -> float:
        return self.now

    def next_code(self, secret: str = TOTP_SECRET) -> str:
        """Move to the next 30-second step and return its code: never a replayed code."""
        self.now += 30
        return pyotp.TOTP(secret).at(self.now)


CLOCK = FakeClock()


async def login(
    client: AsyncClient, email: str, password: str = PASSWORD, *, secret: str = TOTP_SECRET
) -> Response:
    """Log in; for users with two-factor login, also passes the second step."""
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    if response.status_code == 200 and response.json().get("mfa_required"):
        code = CLOCK.next_code(secret)
        response = await client.post("/api/auth/login/verify", json={"code": code})
    return response


async def login_first_step(client: AsyncClient, email: str, password: str = PASSWORD) -> Response:
    return await client.post("/api/auth/login", json={"email": email, "password": password})


async def enable_two_factor(client: AsyncClient) -> tuple[str, list[str]]:
    """Set up two-factor login for the logged-in user; returns (secret, recovery codes)."""
    setup = await client.post("/api/auth/two-factor/setup")
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    enabled = await client.post(
        "/api/auth/two-factor/enable", json={"code": CLOCK.next_code(secret)}
    )
    assert enabled.status_code == 200, enabled.text
    return secret, enabled.json()["recovery_codes"]
