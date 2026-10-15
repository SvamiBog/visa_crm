import uuid
from collections.abc import Awaitable, Callable

from httpx import AsyncClient, Response

PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "staple-and-a-pony-42"

MakeUser = Callable[..., Awaitable[uuid.UUID | None]]


async def login(client: AsyncClient, email: str, password: str = PASSWORD) -> Response:
    return await client.post("/api/auth/login", json={"email": email, "password": password})
