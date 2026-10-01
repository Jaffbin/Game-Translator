from __future__ import annotations

from urllib.parse import urlparse

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}


class LocalOriginMiddleware(BaseHTTPMiddleware):
    """Block cross-site mutations against the unauthenticated local API."""

    async def dispatch(self, request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin:
                parsed = urlparse(origin)
                if parsed.scheme not in {"http", "https"} or parsed.hostname not in LOCAL_HOSTS:
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Cross-origin request rejected."},
                    )
        return await call_next(request)


def add_local_security(app: FastAPI) -> None:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"],
    )
    app.add_middleware(LocalOriginMiddleware)
