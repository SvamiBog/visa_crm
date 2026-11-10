"""The size of a request is limited before the server reads it into memory
(app/core/request_limit.py)."""

from collections.abc import AsyncIterator

from httpx import AsyncClient

from app.core.documents import MAX_BYTES as MAX_DOCUMENT_BYTES
from app.core.request_limit import MAX_REQUEST_BYTES
from app.core.workbook import MAX_BYTES as MAX_IMPORT_BYTES

LOGIN = "/api/auth/login"


def test_the_largest_file_the_crm_takes_fits_into_a_request():
    # A file goes in the JSON body as base64: four characters for every three bytes.
    largest = max(MAX_DOCUMENT_BYTES, MAX_IMPORT_BYTES)
    assert largest * 4 / 3 + 64 * 1024 < MAX_REQUEST_BYTES


async def test_a_request_larger_than_the_limit_is_refused_unread(client: AsyncClient):
    """Nobody is logged in: the body is refused by its declared size, not parsed."""
    response = await client.post(
        LOGIN,
        content=b"x" * (MAX_REQUEST_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json() == {"detail": "request_too_large"}
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_a_body_sent_in_pieces_without_its_size_is_counted(client: AsyncClient):
    async def pieces() -> AsyncIterator[bytes]:
        for _ in range(MAX_REQUEST_BYTES // (1024 * 1024) + 1):
            yield b"x" * (1024 * 1024)

    response = await client.post(
        LOGIN, content=pieces(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json() == {"detail": "request_too_large"}


async def test_a_request_within_the_limit_is_read_as_before(client: AsyncClient):
    response = await client.post(LOGIN, json={"email": "nobody@example.com", "password": "x"})
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_credentials"
