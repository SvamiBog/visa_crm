from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.admin import router as admin_router
from app.api.analytics import router as analytics_router
from app.api.audit import router as audit_router
from app.api.auth import router as auth_router
from app.api.bookings import router as bookings_router
from app.api.cases import router as cases_router
from app.api.checklists import admin_router as admin_checklists_router
from app.api.checklists import router as checklists_router
from app.api.consultations import router as consultations_router
from app.api.contacts import router as contacts_router
from app.api.dictionaries import router as dictionaries_router
from app.api.documents import router as documents_router
from app.api.email_change import router as email_change_router
from app.api.employees import router as employees_router
from app.api.finances import router as finances_router
from app.api.health import router as health_router
from app.api.home import router as home_router
from app.api.imports import router as imports_router
from app.api.mrz import router as mrz_router
from app.api.queues import router as queues_router
from app.api.reference import admin_router as admin_reference_router
from app.api.reference import router as reference_router
from app.api.registrar_ledger import router as registrar_ledger_router
from app.api.reminders import router as reminders_router
from app.api.stay import router as stay_router
from app.api.two_factor import router as two_factor_router
from app.api.vault import router as vault_router
from app.api.workspace import router as workspace_router
from app.core.audit import client_ip
from app.core.config import get_settings
from app.core.http_headers import add_security_headers


def create_app() -> FastAPI:
    app = FastAPI(title="VisaCRM", docs_url="/api/docs", openapi_url="/api/openapi.json")

    @app.middleware("http")
    async def remember_client_ip(request: Request, call_next) -> Response:
        # The audit log records the client address (uvicorn --proxy-headers resolves
        # the real one behind Railway's proxy).
        token = client_ip.set(request.client.host if request.client else None)
        try:
            return await call_next(request)
        finally:
            client_ip.reset(token)

    add_security_headers(app)

    for router in (
        health_router,
        auth_router,
        email_change_router,
        two_factor_router,
        admin_router,
        workspace_router,
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
        consultations_router,
        cases_router,
        queues_router,
        bookings_router,
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
    dist = get_settings().frontend_dist.resolve()
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(status_code=404)
            # resolve() + is_relative_to() blocks "../" paths escaping frontend/dist.
            file = (dist / path).resolve()
            if path and file.is_relative_to(dist) and file.is_file():
                return FileResponse(file)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
