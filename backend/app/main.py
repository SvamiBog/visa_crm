import asyncio
import mimetypes
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import PurePath

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from app.api.admin import router as admin_router
from app.api.analytics import router as analytics_router
from app.api.audit import router as audit_router
from app.api.auth import router as auth_router
from app.api.bookings import router as bookings_router
from app.api.case_access import router as case_access_router
from app.api.cases import router as cases_router
from app.api.checklists import admin_router as admin_checklists_router
from app.api.checklists import router as checklists_router
from app.api.consultations import router as consultations_router
from app.api.contacts import router as contacts_router
from app.api.deps import current_user_any
from app.api.dictionaries import router as dictionaries_router
from app.api.documents import router as documents_router
from app.api.email_change import router as email_change_router
from app.api.employees import router as employees_router
from app.api.finances import router as finances_router
from app.api.health import router as health_router
from app.api.home import router as home_router
from app.api.imports import router as imports_router
from app.api.mrz import router as mrz_router
from app.api.package import router as package_router
from app.api.portal import router as portal_router
from app.api.portal_public import portal_signed_in
from app.api.portal_public import router as portal_public_router
from app.api.queues import router as queues_router
from app.api.reference import admin_router as admin_reference_router
from app.api.reference import router as reference_router
from app.api.registrar_ledger import router as registrar_ledger_router
from app.api.reminders import router as reminders_router
from app.api.stay import router as stay_router
from app.api.subscriptions import admin_router as admin_subscriptions_router
from app.api.subscriptions import router as subscriptions_router
from app.api.templates import contact_router as contact_templates_router
from app.api.templates import router as templates_router
from app.api.two_factor import router as two_factor_router
from app.api.vault import router as vault_router
from app.api.workspace import router as workspace_router
from app.core.audit import client_address, client_ip
from app.core.config import get_settings
from app.core.employee_limits import LimitReached
from app.core.http_headers import add_security_headers, refuse_cross_site_changes
from app.core.passport_purge import keep_purging
from app.core.rates import keep_fresh
from app.core.request_limit import RequestLimit
from app.core.server_errors import UnexpectedErrors

# Vite names what it builds `<name>-<8-character hash>.<ext>`, right in assets/. The page
# names the current files, so it is checked on every visit, and the files themselves are
# never asked for again. Files copied as they are (the passport reader's model in a
# folder of assets/) keep the default caching.
HASHED_ASSET = re.compile(r"-[A-Za-z0-9_-]{8}\.[a-z0-9]+$")
IMMUTABLE = "public, max-age=31536000, immutable"
# A request the API turns down names at most this many fields: a form has far fewer, and a
# long list of bad items is not answered item by item.
MAX_VALIDATION_ERRORS = 100


class BuiltAssets(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        flat = PurePath(path).name == path
        if response.status_code in (200, 304) and flat and HASHED_ASSET.search(path):
            response.headers["Cache-Control"] = IMMUTABLE
        return response


async def signed_in(scope: Scope) -> bool:
    """The API's own session check: a made-up cookie or a login still waiting for its second
    factor gets no upload allowance (app/core/request_limit.py)."""
    try:
        await current_user_any(Request(scope))
    except HTTPException:
        return False
    return True


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # The site keeps the National Bank's rates itself: requests never ask the bank.
    rates = asyncio.create_task(keep_fresh())
    # The passport's data goes a week after the decision (backlog 2.8), checked a few times
    # a day in the same process: no cron service runs the app's code.
    purges = asyncio.create_task(keep_purging())
    yield
    for task in (rates, purges):
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def create_app() -> FastAPI:
    settings = get_settings()
    # The schema maps every endpoint and error code: developers have it, the stands and the
    # real site do not. ReDoc is off everywhere: the site's CSP blocks its scripts.
    docs = settings.api_docs
    app = FastAPI(
        title="VisaCRM",
        docs_url="/api/docs" if docs else None,
        openapi_url="/api/openapi.json" if docs else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    # A request too large for its route is refused before it is read: only a file sent by
    # someone signed in may be large.
    app.add_middleware(RequestLimit, signed_in=signed_in, portal_signed_in=portal_signed_in)
    # An unexpected error is logged without its text (it may quote clients' data). Added
    # after the limit, so it also catches a failed session lookup of the limit.
    app.add_middleware(UnexpectedErrors)
    # A changing request from another site is refused before its body is read or its
    # session looked up; inside the security headers, so the refusal carries them too.
    refuse_cross_site_changes(app)

    @app.middleware("http")
    async def remember_client_ip(request: Request, call_next) -> Response:
        # The audit log and the login limit take the client's address from the X-Real-IP
        # that Railway's edge writes, checked to be an address. uvicorn reads no proxy
        # header (start.sh): it would trust X-Forwarded-For, which anybody can write.
        peer = request.client.host if request.client else None
        address = client_address(request.headers, peer)
        token = client_ip.set(address)
        try:
            return await call_next(request)
        finally:
            client_ip.reset(token)

    add_security_headers(app)

    @app.exception_handler(LimitReached)
    async def limit_reached(request: Request, error: LimitReached) -> JSONResponse:
        # An employee's daily limit (backlog Т.14): paused until the next day.
        return JSONResponse(status_code=429, content={"detail": error.code})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        # The field and the reason, which is what the forms read (components/Field.tsx), not
        # the value: FastAPI's own answer sends the decoded body back, which costs time and
        # memory as it grows and may repeat clients' data.
        detail = [
            {"type": e["type"], "loc": list(e["loc"]), "msg": e["msg"]}
            for e in error.errors()[:MAX_VALIDATION_ERRORS]
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": detail}
        )

    for router in (
        health_router,
        auth_router,
        email_change_router,
        two_factor_router,
        admin_router,
        admin_subscriptions_router,
        workspace_router,
        subscriptions_router,
        home_router,
        audit_router,
        employees_router,
        reference_router,
        admin_reference_router,
        checklists_router,
        admin_checklists_router,
        dictionaries_router,
        contacts_router,
        documents_router,
        templates_router,
        contact_templates_router,
        consultations_router,
        cases_router,
        case_access_router,
        queues_router,
        bookings_router,
        package_router,
        portal_router,
        portal_public_router,
        finances_router,
        registrar_ledger_router,
        reminders_router,
        analytics_router,
        imports_router,
        mrz_router,
        stay_router,
        vault_router,
    ):
        app.include_router(router, prefix="/api")

    # In production the built React app is served from the same origin as the API,
    # so session cookies work without CORS. In local dev Vite serves it and proxies /api.
    dist = settings.frontend_dist.resolve()
    if dist.is_dir():
        # The interface fonts (frontend/src/fonts.css): Python 3.12 does not know the type
        # and would send them as an unnamed binary file.
        mimetypes.add_type("font/woff2", ".woff2")
        # An old build's file is gone after a deploy: 404, which the app answers by
        # reloading (frontend/src/components/LazyPage.tsx).
        app.mount("/assets", BuiltAssets(directory=dist / "assets"), name="assets")
        staging = get_settings().env == "staging"

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> Response:
            if path.startswith("api/"):
                raise HTTPException(status_code=404)
            # resolve() + is_relative_to() blocks "../" paths escaping frontend/dist.
            file = (dist / path).resolve()
            if path and file.is_relative_to(dist) and file.is_file():
                return FileResponse(file)
            index = dist / "index.html"
            headers = {"Cache-Control": "no-cache"}
            if staging:
                # The page carries its mark, so the strip «Тестовый стенд…»
                # (frontend/src/App.tsx) is drawn with the first paint and pushes nothing down.
                page = await asyncio.to_thread(index.read_bytes)
                page = page.replace(b"<html ", b'<html data-env="staging" ', 1)
                return Response(page, media_type="text/html", headers=headers)
            return FileResponse(index, headers=headers)

    return app


app = create_app()
