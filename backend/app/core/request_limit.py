"""A limit on the size of a request.

Every request body is JSON read whole into memory before it is checked, the login's too:
without a limit anyone, logged in or not, could send hundreds of megabytes and leave the
one web process without memory. Files come inside the JSON as base64 (a third larger than
the file), and the largest file the CRM takes is 10 MB (app/core/documents.py,
app/core/workbook.py), so the limit leaves room for it; the limits of the files themselves
stay where they are and answer with their own codes.
"""

from fastapi import HTTPException, status
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_REQUEST_BYTES = 16 * 1024 * 1024
TOO_LARGE = "request_too_large"


class RequestLimit:
    """ASGI middleware: 413 for a request that says it is larger than the limit, before any
    of it is read, and for one that turns out to be while it is read."""

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_REQUEST_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = Headers(scope=scope).get("content-length", "")
        if declared.isdigit() and int(declared) > self.max_bytes:
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
                if received > self.max_bytes:
                    raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, TOO_LARGE)
            return message

        await self.app(scope, counted, send)
