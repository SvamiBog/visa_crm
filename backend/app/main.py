from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.workspace import router as workspace_router
from app.core.config import get_settings


def create_app() -> FastAPI:
    app = FastAPI(title="VisaCRM", docs_url="/api/docs", openapi_url="/api/openapi.json")
    for router in (health_router, auth_router, admin_router, workspace_router):
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
