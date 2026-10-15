from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.admin import router as admin_router
from app.api.audit import router as audit_router
from app.api.auth import router as auth_router
from app.api.employees import router as employees_router
from app.api.health import router as health_router
from app.api.reference import admin_router as admin_reference_router
from app.api.reference import router as reference_router
from app.api.two_factor import router as two_factor_router
from app.api.workspace import router as workspace_router
from app.core.audit import client_ip
from app.core.config import get_settings


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

    for router in (
        health_router,
        auth_router,
        two_factor_router,
        admin_router,
        workspace_router,
        audit_router,
        employees_router,
        reference_router,
        admin_reference_router,
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
