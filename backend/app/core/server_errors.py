"""Errors a request did not expect: a plain 500 for the client, and a log line without
clients' data (audit of the code, roadmap 6.2).

The text of an error may quote clients' data: asyncpg names the value it could not send,
a unique key names the duplicate. Starlette answers 500 but raises the error again, and
uvicorn then logs it whole as «Exception in ASGI application», so the error stops here.
The log gets the type, the handler and the frames it went through, never the message.
"""

import logging
import traceback

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


def frames(error: BaseException) -> str:
    """Where the error went, one frame a line: file, line and function, no values."""
    return "\n".join(
        f"  {frame.filename}:{frame.lineno} in {frame.name}"
        for frame in traceback.extract_tb(error.__traceback__)
    )


class UnexpectedErrors:
    """ASGI middleware. Added first, so it sits inside the others and its 500 still gets
    the security headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def watched(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, receive, watched)
        except Exception as error:
            # The handler's name, not the path: no ids in the log either.
            endpoint = scope.get("endpoint")
            handler = f"{endpoint.__module__}.{endpoint.__qualname__}" if endpoint else "-"
            logger.error(
                "%s %s failed: %s\n%s",
                scope["method"],
                handler,
                type(error).__name__,
                frames(error),
            )
            # Once the answer has begun it can only be cut short.
            if not started:
                response = PlainTextResponse("Internal Server Error", status_code=500)
                await response(scope, receive, send)
