"""A limit on the size of a request (backlog S1, audit finding M7).

FastAPI reads a JSON body whole and decodes it before any dependency runs, the session
check included, and the decoded objects take tens of times the body's size in memory and
hold up the one process meanwhile. So the size is checked here, before the app reads
anything: small for every form, large only for a route that takes a file and only for a
caller who is signed in. A cookie alone proves nothing, anyone can make one up, so the
session itself is looked up.

The largest form, the questionnaire, stays under 230 KB even at four bytes a character.
Files come inside the JSON as base64 (a third larger than the file), and the largest file
the CRM takes is 10 MB (app/core/documents.py, app/core/workbook.py), so the upload limit
leaves room for it; the limits of the files themselves stay where they are and answer
with their own codes.
"""

import re
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, status
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_FORM_BYTES = 256 * 1024
MAX_UPLOAD_BYTES = 16 * 1024 * 1024
# Files of the client portal go up to 20 MB (app/core/portal.py), 27 MB in base64.
MAX_PORTAL_UPLOAD_BYTES = 28 * 1024 * 1024
TOO_LARGE = "request_too_large"

_ID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
# The routes whose body carries a file; the tests hold the list to the API's schema.
UPLOADS = (
    ("POST", re.compile(rf"/api/contacts/{_ID}/documents")),
    ("POST", re.compile(r"/api/imports/uploads")),
    ("POST", re.compile(r"/api/templates")),
    ("PUT", re.compile(rf"/api/templates/{_ID}/file")),
)
# Files at checklist items (backlog 2.9): from the case, for a signed-in member, and from
# the client portal, for a client signed in to that link with a code.
ITEM_UPLOADS = (("POST", re.compile(rf"/api/cases/{_ID}/package/{_ID}/files")),)
PORTAL_UPLOADS = (("POST", re.compile(rf"/api/portal/[A-Za-z0-9_-]{{43}}/items/{_ID}/files")),)

# Whether the request comes with a live session, as the API's own session check sees it.
SignedIn = Callable[[Scope], Awaitable[bool]]


def _matches(routes: tuple[tuple[str, re.Pattern[str]], ...], method: str, path: str) -> bool:
    return any(method == m and pattern.fullmatch(path) for m, pattern in routes)


def takes_a_file(method: str, path: str) -> bool:
    return any(_matches(r, method, path) for r in (UPLOADS, ITEM_UPLOADS, PORTAL_UPLOADS))


class RequestLimit:
    """ASGI middleware: 413 for a request that says it is larger than its limit, before any
    of it is read, and for one that turns out to be while it is read."""

    def __init__(self, app: ASGIApp, *, signed_in: SignedIn, portal_signed_in: SignedIn) -> None:
        self.app = app
        self.signed_in = signed_in
        self.portal_signed_in = portal_signed_in

    async def limit(self, scope: Scope) -> int:
        method, path = scope["method"], scope["path"]
        if _matches(UPLOADS, method, path) and await self.signed_in(scope):
            return MAX_UPLOAD_BYTES
        if _matches(ITEM_UPLOADS, method, path) and await self.signed_in(scope):
            return MAX_PORTAL_UPLOAD_BYTES
        if _matches(PORTAL_UPLOADS, method, path) and await self.portal_signed_in(scope):
            return MAX_PORTAL_UPLOAD_BYTES
        return MAX_FORM_BYTES

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = await self.limit(scope)
        declared = Headers(scope=scope).get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            response = JSONResponse(
                {"detail": TOO_LARGE}, status_code=status.HTTP_413_CONTENT_TOO_LARGE
            )
            await response(scope, receive, send)
            return
        received = 0

        async def counted() -> Message:
            # A body sent in pieces declares no size: it is counted as it comes. The
            # handler reading it gets the error and the app answers with its code.
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, TOO_LARGE)
            return message

        await self.app(scope, counted, send)
