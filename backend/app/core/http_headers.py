"""Security headers on every response, and where a changing request may come from.

The page may load scripts, styles and fonts only from its own origin (and compile
WebAssembly, not eval strings), cannot be framed, and API responses are not cached
(they carry clients' data). Outside production
the site asks search engines not to index it: the staging stand must not be found.

A request that changes something (POST, PUT, PATCH, DELETE) is taken only from the site's
own pages (audit h01.3, backlog S19). The session cookie is SameSite=Lax already; this does
not rest on how a browser treats that cookie, and it also refuses a neighbouring subdomain
of the same hosting, which is the same site but not the same origin.
"""

import logging
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers

from app.core.config import get_settings

logger = logging.getLogger(__name__)

CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        # WebAssembly, not eval: the passport reader runs its engine in the browser so the
        # photo never leaves the device (frontend/src/ocr.ts).
        "script-src 'self' 'wasm-unsafe-eval'",
        # The fonts are ours too (frontend/src/fonts.css): nothing asks Google, so a
        # specialist's IP address does not reach it.
        "style-src 'self'",
        "font-src 'self'",
        "img-src 'self' data:",  # the two-factor QR code is a data: image
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)
# Swagger UI (a developer's machine only, Settings.api_docs) loads its scripts from a CDN.
WITHOUT_CSP = ("/api/docs",)
# The client portal (backlog 2.9): its page and its API are never indexed, in production
# too, and send no Referer.
PORTAL_PATHS = ("/u/", "/api/portal/")
CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# What a browser names in Sec-Fetch-Site for the site's own requests: sent by its own page,
# or started by the user (typed, bookmarked) rather than by any page.
OWN_FETCH_SITES = frozenset({"same-origin", "none"})


def sent_from_own_page(headers: Headers) -> bool:
    """Whether a changing request comes from the site itself.

    A current browser names the sender in Sec-Fetch-Site. An older one sends only Origin,
    which is compared with the host the request was sent to: Host, or the public host a
    proxy names in X-Forwarded-Host. A page cannot set either header on a request to
    another site (X-Forwarded-Host would need a CORS preflight the site never allows).
    A request with neither is not from a browser (a script, the CLI): no browser added a
    cookie to it on its own, so it cannot be a forged one, and it is let through.
    """
    fetch_site = headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site in OWN_FETCH_SITES
    origin = headers.get("origin")
    if origin is None:
        return True
    sender = urlsplit(origin).netloc.lower()  # "" for "null"
    own = [headers.get("host", ""), *headers.get("x-forwarded-host", "").split(",")]
    return bool(sender) and sender in {host.strip().lower() for host in own}


def refuse_cross_site_changes(app: FastAPI) -> None:
    @app.middleware("http")
    async def own_pages_only(request: Request, call_next) -> Response:
        if request.method in CHANGING_METHODS and not sent_from_own_page(request.headers):
            # Which header decided, so a proxy that renames the host shows up in the log
            # after a deploy («by Origin» for the site's own work). Not the path: no ids in
            # the log, and nothing the sender wrote.
            by = "Sec-Fetch-Site" if "sec-fetch-site" in request.headers else "Origin"
            logger.warning("Refused a %s from another site, by %s", request.method, by)
            return JSONResponse(status_code=403, content={"detail": "forbidden"})
        return await call_next(request)


def add_security_headers(app: FastAPI) -> None:
    @app.middleware("http")
    async def security_headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        settings = get_settings()
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        path = request.url.path
        if not (settings.api_docs and path.startswith(WITHOUT_CSP)):
            headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        if path.startswith("/api/"):
            headers.setdefault("Cache-Control", "no-store")
        if settings.cookie_secure:  # served over HTTPS
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if settings.env != "production" or path.startswith(PORTAL_PATHS):
            headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        if path.startswith(PORTAL_PATHS):
            # The address carries the link's token: no other site gets it in a Referer.
            headers["Referrer-Policy"] = "no-referrer"
        return response
