"""Security headers on every response.

The page may load scripts only from its own origin (and compile WebAssembly, not eval
strings) and fonts from Google Fonts, cannot be framed, and API responses are not cached
(they carry clients' data). Outside production
the site asks search engines not to index it: the staging stand must not be found.
"""

from fastapi import FastAPI, Request, Response

from app.core.config import get_settings

CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        # WebAssembly, not eval: the passport reader runs its engine in the browser so the
        # photo never leaves the device (frontend/src/ocr.ts).
        "script-src 'self' 'wasm-unsafe-eval'",
        "style-src 'self' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data:",  # the two-factor QR code is a data: image
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)
# Swagger UI loads its scripts from a CDN.
WITHOUT_CSP = ("/api/docs",)


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
        if not path.startswith(WITHOUT_CSP):
            headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        if path.startswith("/api/"):
            headers.setdefault("Cache-Control", "no-store")
        if settings.cookie_secure:  # served over HTTPS
            headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if settings.env != "production":
            headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        return response
