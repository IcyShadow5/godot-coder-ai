from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .. import __version__
from ..remote_access import RemoteAccessManager
from ..chat_store import ChatStore
from .jobs import JobManager
from .routers import (
    build_chat_router,
    build_corpus_router,
    build_remote_router,
    build_system_router,
    build_training_router,
)
from .services import GenerationService


class _SecureRemoteMiddleware:
    """Pure ASGI middleware for remote-access authorization and security headers.

    Avoids Starlette's BaseHTTPMiddleware which has a known Content-Length
    mismatch bug with streaming responses (h11 LocalProtocolError).
    """

    def __init__(self, app: ASGIApp, remote_access) -> None:
        self.app = app
        self.remote_access = remote_access

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Build a request to inspect headers/cookies/path
        request = Request(scope, receive)
        path = request.url.path

        # Remote access check
        denial = self.remote_access.authorize(
            method=request.method,
            path=path,
            headers=request.headers,
            cookies=request.cookies,
        )
        if denial is not None:
            status_code, detail = denial
            response = JSONResponse(status_code=status_code, content={"detail": detail})
            # Apply security headers via the Starlette response object
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("Referrer-Policy", "no-referrer")
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self'; connect-src 'self'; worker-src 'self'; manifest-src 'self'; frame-ancestors 'none'",
            )
            if path.startswith("/api/"):
                response.headers.setdefault("Cache-Control", "no-store")
            await response(scope, receive, send)
            return

        # Intercept the response start to inject security headers,
        # then forward all messages to the original send without
        # re-wrapping the body (avoids BaseHTTPMiddleware bug).
        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message.get("headers", []))
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "no-referrer")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
                headers.setdefault(
                    "Content-Security-Policy",
                    "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; "
                    "script-src 'self'; connect-src 'self'; worker-src 'self'; manifest-src 'self'; frame-ancestors 'none'",
                )
                if path.startswith("/api/"):
                    headers.setdefault("Cache-Control", "no-store")
                message["headers"] = headers.raw
            await send(message)

        await self.app(scope, receive, send_with_headers)

        # Audit non-GET remote writes (fire-and-forget after response).
        # Note: status_code is not available here since the response was
        # already streamed; the old BaseHTTPMiddleware captured it but
        # that pattern caused the Content-Length bug.
        identity = self.remote_access.identity(request.headers).get("login")
        if identity and request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            await asyncio.to_thread(
                self.remote_access.audit,
                "remote_write_request",
                identity=identity,
                method=request.method.upper(),
                path=path,
            )


def create_app(project_root: Path) -> FastAPI:
    root = project_root.resolve()
    static_dir = Path(__file__).resolve().parent / "static"

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            yield
        finally:
            await asyncio.to_thread(application.state.jobs.stop)
            await asyncio.to_thread(application.state.generation.unload)

    app = FastAPI(
        title="Godot Coder Studio",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.project_root = root
    app.state.jobs = JobManager(root)
    app.state.generation = GenerationService(root)
    app.state.chat_store = ChatStore(root)
    app.state.remote_access = RemoteAccessManager(root)

    # Pure ASGI middleware: avoids BaseHTTPMiddleware Content-Length bug
    app.add_middleware(_SecureRemoteMiddleware, remote_access=app.state.remote_access)

    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def web_manifest() -> FileResponse:
        return FileResponse(static_dir / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/sw.js", include_in_schema=False)
    async def service_worker() -> FileResponse:
        return FileResponse(static_dir / "sw.js", media_type="application/javascript")


    # Route groups live in ui/routers/ so each area stays readable on its own.
    app.include_router(build_remote_router(app))
    app.include_router(build_corpus_router(app))
    app.include_router(build_training_router(app))
    app.include_router(build_chat_router(app))
    app.include_router(build_system_router(app))

    return app
