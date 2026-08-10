from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ...remote_access import RemoteAccessError, SESSION_COOKIE, remote_self_check, request_is_remote
from ...remote_sources import MAX_REMOTE_SOURCE_BYTES, RemoteSourceError, stage_uploaded_zip, validate_remote_url
from ..schemas import RemoteDownloadRequest, RemoteUnlockRequest


def build_remote_router(app: FastAPI) -> APIRouter:
    root = app.state.project_root
    router = APIRouter(tags=["remote"])

    @router.get("/api/remote/status")
    async def remote_status(request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(app.state.remote_access.status, request.headers, request.cookies)

    @router.get("/api/remote/self-check")
    async def remote_check(request: Request) -> dict[str, Any]:
        # A local CLI port override should be tested as it is actually running.
        # Through Tailscale the public request port is normally 443, so remote
        # calls intentionally use the persisted localhost backend port instead.
        local_port = request.url.port if not request_is_remote(request.headers) else None
        return await asyncio.to_thread(remote_self_check, root, port=local_port)

    @router.post("/api/remote/unlock")
    async def remote_unlock(request: Request, payload: RemoteUnlockRequest) -> JSONResponse:
        try:
            session = await asyncio.to_thread(app.state.remote_access.unlock, request.headers, payload.pin)
        except RemoteAccessError as exc:
            raise HTTPException(status_code=429 if "Too many failed attempts" in str(exc) else 403, detail=str(exc)) from exc
        response = JSONResponse({
            "unlocked": True,
            "csrf_token": session.csrf_token,
            "expires_at": session.expires_at,
        })
        response.set_cookie(
            SESSION_COOKIE,
            session.token,
            max_age=max(1, int(session.expires_at - time.time())),
            secure=True,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return response

    @router.post("/api/remote/lock")
    async def remote_lock(request: Request) -> JSONResponse:
        identity = app.state.remote_access.identity(request.headers).get("login")
        await asyncio.to_thread(app.state.remote_access.lock, request.cookies.get(SESSION_COOKIE), identity)
        response = JSONResponse({"locked": True})
        response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="strict")
        return response

    @router.post("/api/jobs/remote/source-download")
    async def remote_source_download(request: RemoteDownloadRequest) -> dict[str, Any]:
        if not request.confirm_owned:
            raise HTTPException(status_code=400, detail="First confirm that you are allowed to use the source code locally.")
        try:
            normalized = await asyncio.to_thread(validate_remote_url, request.url)
            return app.state.jobs.start(
                "remote-source-download",
                ["-m", "godot_coder.remote_sources", "--root", str(root), "download", "--url", normalized],
            )
        except RemoteSourceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/api/remote/sources/upload")
    async def remote_source_upload(
        request: Request,
        filename: str = Query(..., min_length=1, max_length=180),
        confirm_owned: bool = Query(default=False),
    ) -> dict[str, Any]:
        if not confirm_owned:
            raise HTTPException(status_code=400, detail="First confirm that you are allowed to use the source code locally.")
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > MAX_REMOTE_SOURCE_BYTES:
            raise HTTPException(status_code=413, detail=f"Upload exceeds {MAX_REMOTE_SOURCE_BYTES // 1024**2} MiB.")
        staging = root / "data" / "local_sources" / ".remote_staging"
        staging.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix="upload-", suffix=".zip.part", dir=staging)
        os.close(descriptor)
        temporary = Path(temporary_name)
        received = 0
        try:
            with temporary.open("wb") as output:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > MAX_REMOTE_SOURCE_BYTES:
                        raise HTTPException(status_code=413, detail=f"Upload exceeds {MAX_REMOTE_SOURCE_BYTES // 1024**2} MiB.")
                    output.write(chunk)
            result = await asyncio.to_thread(stage_uploaded_zip, root, temporary, filename)
            app.state.remote_access.audit(
                "remote_source_uploaded",
                identity=app.state.remote_access.identity(request.headers).get("login"),
                name=result["name"],
                size_bytes=result["size_bytes"],
            )
            return result
        except RemoteSourceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            temporary.unlink(missing_ok=True)

    return router
