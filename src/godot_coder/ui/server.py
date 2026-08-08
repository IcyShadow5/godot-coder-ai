from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .. import __version__
from ..config import ModelConfig, TrainConfig, load_config
from .jobs import JobManager
from ..corpus import load_registry, save_registry, status as corpus_status
from ..corpus_audit import build_preflight
from ..local_sources import open_inbox as open_local_inbox, status as local_source_status
from ..remote_access import RemoteAccessError, RemoteAccessManager, SESSION_COOKIE, remote_self_check, request_is_remote
from ..remote_sources import MAX_REMOTE_SOURCE_BYTES, RemoteSourceError, stage_uploaded_zip, validate_remote_url
from .paths import safe_child
from .services import (
    GenerationService,
    build_data_catalog,
    delete_dataset_file,
    list_checkpoints,
    list_configs,
    list_dataset_files,
    list_training_reports,
    read_curriculum_status,
    read_dataset_file,
    read_manifest,
    read_vram_probe,
    relative_posix,
    system_status,
    validate_code,
    write_dataset_file,
)


class GenerateRequest(BaseModel):
    checkpoint: str
    prompt: str
    max_new_tokens: int = Field(default=300, ge=1, le=4096)
    temperature: float = Field(default=0.4, ge=0.0, le=5.0)
    top_k: int = Field(default=10, ge=0, le=1000)
    device: str = "auto"


class ValidateRequest(BaseModel):
    code: str
    project: str = "data/raw/seed_project"


class FileWriteRequest(BaseModel):
    path: str
    content: str


class TrainRequest(BaseModel):
    config: str
    resume: str | None = None


class PrepareRequest(BaseModel):
    input_dir: str = "data/raw"
    output_dir: str = "data/processed"
    tokenizer: str = "artifacts/tokenizer.json"
    val_ratio: float = Field(default=0.15, gt=0.0, lt=1.0)


class BenchmarkRequest(BaseModel):
    checkpoint: str


class CorpusSourceRequest(BaseModel):
    sources: list[dict[str, Any]]


class CorpusTokenizerRequest(BaseModel):
    vocab_size: int = Field(default=8192, ge=512, le=32768)
    min_frequency: int = Field(default=2, ge=1, le=100)


class LocalSourceImportRequest(BaseModel):
    confirm_owned: bool = False
    skip_project_import: bool = False
    fast_static: bool = False
    error_abort_threshold: int | None = Field(default=None, ge=50, le=10000)


def _local_import_extra_env(
    *,
    skip_project_import: bool,
    fast_static: bool,
    error_abort_threshold: int | None,
) -> dict[str, str]:
    """Map Studio toggle flags to the env vars local_sources understands.

    These knobs used to be CLI/env-only; the Studio now forwards them to the
    import child process so the fast-import options don't need a shell.
    """
    extra: dict[str, str] = {}
    if skip_project_import:
        extra["GODOT_CODER_SKIP_PROJECT_IMPORT"] = "1"
    if fast_static:
        extra["GODOT_CODER_FAST_STATIC"] = "1"
    if error_abort_threshold is not None:
        extra["GODOT_CODER_ERROR_ABORT_THRESHOLD"] = str(error_abort_threshold)
    return extra


class RemoteUnlockRequest(BaseModel):
    pin: str = Field(min_length=6, max_length=12, pattern=r"^\d+$")


class RemoteDownloadRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    confirm_owned: bool = False


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

    @app.get("/api/remote/status")
    async def remote_status(request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(app.state.remote_access.status, request.headers, request.cookies)

    @app.get("/api/remote/self-check")
    async def remote_check(request: Request) -> dict[str, Any]:
        # A local CLI port override should be tested as it is actually running.
        # Through Tailscale the public request port is normally 443, so remote
        # calls intentionally use the persisted localhost backend port instead.
        local_port = request.url.port if not request_is_remote(request.headers) else None
        return await asyncio.to_thread(remote_self_check, root, port=local_port)

    @app.post("/api/remote/unlock")
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

    @app.post("/api/remote/lock")
    async def remote_lock(request: Request) -> JSONResponse:
        identity = app.state.remote_access.identity(request.headers).get("login")
        await asyncio.to_thread(app.state.remote_access.lock, request.cookies.get(SESSION_COOKIE), identity)
        response = JSONResponse({"locked": True})
        response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="strict")
        return response

    @app.get("/api/overview")
    async def overview() -> dict[str, Any]:
        base = await asyncio.to_thread(system_status, root)
        # Bundle frequently-requested data to reduce frontend round-trips
        base["checkpoints"] = await asyncio.to_thread(list_checkpoints, root)
        base["configs"] = await asyncio.to_thread(list_configs, root)
        base["latest_training_reports"] = await asyncio.to_thread(list_training_reports, root)
        return base

    @app.get("/api/configs")
    async def configs() -> list[dict[str, Any]]:
        return await asyncio.to_thread(list_configs, root)

    @app.get("/api/checkpoints")
    async def checkpoints() -> list[dict[str, Any]]:
        return await asyncio.to_thread(list_checkpoints, root)

    @app.get("/api/hardware/probe")
    async def hardware_probe() -> dict[str, Any] | None:
        return await asyncio.to_thread(read_vram_probe, root)

    @app.get("/api/hardware/autotune")
    async def hardware_autotune() -> dict[str, Any] | None:
        path = root / "reports" / "hardware" / "autotune_latest.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None

    @app.get("/api/training/reports")
    async def training_reports() -> list[dict[str, Any]]:
        return await asyncio.to_thread(list_training_reports, root)

    @app.get("/api/data/files")
    async def data_files() -> list[dict[str, Any]]:
        return await asyncio.to_thread(list_dataset_files, root)

    @app.get("/api/data/catalog")
    async def data_catalog() -> dict[str, Any]:
        return await asyncio.to_thread(build_data_catalog, root)

    @app.get("/api/data/file")
    async def data_file(path: str = Query(..., min_length=1)) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(read_dataset_file, root, path)
        except (ValueError, FileNotFoundError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/api/data/file")
    async def save_data_file(request: FileWriteRequest) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(write_dataset_file, root, request.path, request.content)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/data/file")
    async def remove_data_file(path: str = Query(..., min_length=1)) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(delete_dataset_file, root, path)
        except (ValueError, FileNotFoundError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/data/manifest")
    async def manifest() -> dict[str, Any] | None:
        return await asyncio.to_thread(read_manifest, root)

    @app.get("/api/curriculum/status")
    async def curriculum_status() -> dict[str, Any]:
        return await asyncio.to_thread(read_curriculum_status, root)


    @app.get("/api/corpus/status")
    async def get_corpus_status() -> dict[str, Any]:
        return await asyncio.to_thread(corpus_status, root)


    @app.get("/api/corpus/local")
    async def get_local_sources() -> dict[str, Any]:
        return await asyncio.to_thread(local_source_status, root)

    @app.post("/api/corpus/local/open")
    async def open_local_sources_inbox() -> dict[str, Any]:
        try:
            await asyncio.to_thread(open_local_inbox, root)
            return {"opened": True, "path": str(root / "data" / "local_sources" / "inbox")}
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/jobs/corpus/local-import")
    async def import_local_sources(request: LocalSourceImportRequest) -> dict[str, Any]:
        if not request.confirm_owned:
            raise HTTPException(status_code=400, detail="First confirm that you are allowed to use the imported code.")
        try:
            current = await asyncio.to_thread(local_source_status, root)
            item_count = len(current.get("inbox_items", [])) or None
            return app.state.jobs.start(
                "local-source-import",
                ["-m", "godot_coder.local_sources", "--root", str(root), "import", "--confirm-owned"],
                max_steps=item_count,
                extra_env=_local_import_extra_env(
                    skip_project_import=request.skip_project_import,
                    fast_static=request.fast_static,
                    error_abort_threshold=request.error_abort_threshold,
                ),
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


    @app.post("/api/jobs/remote/source-download")
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

    @app.post("/api/remote/sources/upload")
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

    @app.get("/api/preflight")
    async def get_preflight(
        config: str | None = None,
        mode: str = Query(default="full", pattern="^(full|smoke)$"),
    ) -> dict[str, Any]:
        try:
            if config:
                config_path = safe_child(root, config, must_exist=True)
            else:
                recommended = root / "configs" / "autotuned_night.yaml"
                balanced = root / "configs" / "corpus_balanced_90m.yaml"
                starter = root / "configs" / "corpus_starter_30m.yaml"
                config_path = next((candidate.resolve() for candidate in (recommended, balanced, starter) if candidate.exists()), None)
            if config_path is not None:
                config_path.relative_to((root / "configs").resolve())
            return await asyncio.to_thread(build_preflight, root, config_path=config_path, mode=mode)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


    @app.put("/api/corpus/sources")
    async def update_corpus_sources(request: CorpusSourceRequest) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(save_registry, root, request.sources)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/jobs/corpus/fetch")
    async def corpus_fetch() -> dict[str, Any]:
        try:
            registry = await asyncio.to_thread(load_registry, root)
            enabled = sum(bool(item.get("enabled")) for item in registry["sources"])
            return app.state.jobs.start(
                "corpus-download",
                ["-m", "godot_coder.corpus", "--root", str(root), "fetch"],
                max_steps=enabled or None,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409 if isinstance(exc, RuntimeError) else 400, detail=str(exc)) from exc

    @app.post("/api/jobs/corpus/build")
    async def corpus_build() -> dict[str, Any]:
        try:
            return app.state.jobs.start(
                "corpus-scan",
                ["-m", "godot_coder.corpus", "--root", str(root), "build"],
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/jobs/corpus/validate")
    async def corpus_validate() -> dict[str, Any]:
        try:
            current = await asyncio.to_thread(corpus_status, root)
            records = len((current.get("manifest") or {}).get("records", [])) or None
            return app.state.jobs.start(
                "corpus-validate",
                ["-m", "godot_coder.corpus", "--root", str(root), "validate"],
                max_steps=records,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/jobs/corpus/audit")
    async def corpus_audit() -> dict[str, Any]:
        try:
            return app.state.jobs.start(
                "corpus-audit",
                ["-m", "godot_coder.corpus_audit", "--root", str(root), "audit"],
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/jobs/corpus/tokenizer")
    async def corpus_tokenizer(request: CorpusTokenizerRequest) -> dict[str, Any]:
        try:
            return app.state.jobs.start(
                "corpus-tokenizer",
                [
                    "-m", "godot_coder.corpus", "--root", str(root), "train-bpe",
                    "--vocab-size", str(request.vocab_size),
                    "--min-frequency", str(request.min_frequency),
                ],
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/jobs/corpus/instructions")
    async def corpus_instructions() -> dict[str, Any]:
        try:
            return app.state.jobs.start(
                "instruction-seed-build",
                ["-m", "godot_coder.instruction_data", "--root", str(root), "build"],
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/corpus/scale-plan")
    async def corpus_scale_plan() -> dict[str, Any]:
        from ..scale_plan import build_scale_plan
        return await asyncio.to_thread(build_scale_plan, root)

    @app.post("/api/jobs/corpus/prepare")
    async def corpus_prepare() -> dict[str, Any]:
        try:
            return app.state.jobs.start(
                "corpus-prepare",
                [
                    "-m", "godot_coder.prepare_data",
                    "--input", str(root / "data" / "corpus" / "audited"),
                    "--output", str(root / "data" / "processed" / "corpus_v06"),
                    "--tokenizer", str(root / "artifacts" / "tokenizer_bpe_godot.json"),
                ],
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/chat/generate")
    async def generate(request: GenerateRequest) -> dict[str, Any]:
        current = app.state.jobs.current()
        if current and current["status"] in {"starting", "running", "stopping"}:
            raise HTTPException(status_code=409, detail="Stop the active training/data job before generating.")
        try:
            text = await asyncio.to_thread(
                app.state.generation.generate,
                request.checkpoint,
                request.prompt,
                max_new_tokens=request.max_new_tokens,
                temperature=request.temperature,
                top_k=request.top_k,
                device_name=request.device,
            )
            return {"text": text, "checkpoint": request.checkpoint}
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/chat/generate-stream")
    async def generate_stream(request: GenerateRequest):
        current = app.state.jobs.current()
        if current and current["status"] in {"starting", "running", "stopping"}:
            raise HTTPException(status_code=409, detail="Stop the active training/data job before generating.")

        async def token_stream():
            import torch
            try:
                # Load model once for this stream
                text = await asyncio.to_thread(
                    app.state.generation.generate,
                    request.checkpoint,
                    request.prompt,
                    max_new_tokens=request.max_new_tokens,
                    temperature=request.temperature,
                    top_k=request.top_k,
                    device_name=request.device,
                )
                # Split into token-sized chunks for streaming display
                # For now, stream character-by-character since the model generates all at once.
                # A true token-level streaming generator would require modifying the model.
                chunk_size = max(1, len(text) // 20) if len(text) > 20 else 1
                for i in range(0, len(text), chunk_size):
                    token_json = json.dumps({"token": text[i:i + chunk_size]})
                    yield f"data: {token_json}\n"
                    await asyncio.sleep(0.01)
                yield "data: [DONE]\n"
            except Exception as exc:
                err = json.dumps({"error": str(exc)})
                yield f"data: {err}\n"

        return StreamingResponse(token_stream(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        })

    @app.post("/api/chat/validate")
    async def validate(request: ValidateRequest) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(validate_code, root, request.code, request.project)
        except (ValueError, FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/jobs/hardware/probe")
    async def start_hardware_probe() -> dict[str, Any]:
        try:
            await asyncio.to_thread(app.state.generation.unload)
            return app.state.jobs.start(
                "hardware-profile-probe",
                ["-m", "godot_coder.profile_probe", "--root", str(root)],
                max_steps=3,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/jobs/hardware/autotune")
    async def start_hardware_autotune() -> dict[str, Any]:
        try:
            await asyncio.to_thread(app.state.generation.unload)
            return app.state.jobs.start(
                "hardware-autotune",
                ["-m", "godot_coder.autotune", "--root", str(root), "--full"],
                max_steps=80,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/jobs/train-smoke")
    async def start_training_smoke(request: TrainRequest) -> dict[str, Any]:
        try:
            config_path = safe_child(root, request.config, must_exist=True)
            config_path.relative_to((root / "configs").resolve())
            load_config(config_path)
            readiness = await asyncio.to_thread(build_preflight, root, config_path=config_path, mode="smoke")
            if not readiness.get("can_start"):
                raise ValueError("Smoke test blocked: " + " · ".join(readiness.get("blockers") or ["Preflight failed"]))
            await asyncio.to_thread(app.state.generation.unload)
            return app.state.jobs.start(
                "training-smoke-50",
                ["-m", "godot_coder.train", "--config", str(config_path), "--max-steps", "50"],
                max_steps=50,
            )
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            raise HTTPException(status_code=400 if not isinstance(exc, RuntimeError) else 409, detail=str(exc)) from exc

    @app.post("/api/jobs/train")
    async def start_training(request: TrainRequest) -> dict[str, Any]:
        try:
            config_path = safe_child(root, request.config, must_exist=True)
            config_path.relative_to((root / "configs").resolve())
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            load_config(config_path)
            readiness = await asyncio.to_thread(build_preflight, root, config_path=config_path, mode="full")
            if not readiness.get("can_start"):
                raise ValueError("Training blocked: " + " · ".join(readiness.get("blockers") or ["Preflight failed"]))
            max_steps = int(raw.get("train", {}).get("max_steps", 0)) or None
            await asyncio.to_thread(app.state.generation.unload)
            args = ["-m", "godot_coder.train", "--config", str(config_path)]
            if request.resume:
                resume = safe_child(root, request.resume, must_exist=True)
                resume.relative_to((root / "checkpoints").resolve())
                args.extend(["--resume", str(resume)])
            return app.state.jobs.start("training", args, max_steps=max_steps)
        except (ValueError, FileNotFoundError, RuntimeError, yaml.YAMLError) as exc:
            raise HTTPException(status_code=400 if not isinstance(exc, RuntimeError) else 409, detail=str(exc)) from exc

    @app.post("/api/jobs/curriculum/build")
    async def build_curriculum() -> dict[str, Any]:
        try:
            args = [
                "-m",
                "godot_coder.curriculum",
                "--output",
                str(root / "data" / "raw" / "curriculum_v03"),
                "--overwrite",
            ]
            return app.state.jobs.start("curriculum-build", args)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/jobs/curriculum/validate")
    async def validate_curriculum() -> dict[str, Any]:
        try:
            args = [
                "-m",
                "godot_coder.validate_dataset",
                "--input",
                str(root / "data" / "raw" / "curriculum_v03"),
            ]
            return app.state.jobs.start("curriculum-validate", args, max_steps=192)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/jobs/curriculum/prepare")
    async def prepare_curriculum() -> dict[str, Any]:
        try:
            args = [
                "-m",
                "godot_coder.prepare_data",
                "--input",
                str(root / "data" / "raw" / "curriculum_v03"),
                "--output",
                str(root / "data" / "processed" / "curriculum_v03"),
                "--tokenizer",
                str(root / "artifacts" / "tokenizer.json"),
            ]
            return app.state.jobs.start("curriculum-prepare", args)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/jobs/benchmark")
    async def start_benchmark(request: BenchmarkRequest) -> dict[str, Any]:
        try:
            await asyncio.to_thread(app.state.generation.unload)
            checkpoint = safe_child(root, request.checkpoint, must_exist=True)
            checkpoint.relative_to((root / "checkpoints").resolve())
            args = [
                "-m",
                "godot_coder.benchmark",
                "--project-root",
                str(root),
                "--checkpoint",
                relative_posix(checkpoint, root),
            ]
            return app.state.jobs.start("benchmark", args, max_steps=16)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/jobs/prepare")
    async def start_prepare(request: PrepareRequest) -> dict[str, Any]:
        try:
            input_dir = safe_child(root, request.input_dir, must_exist=True)
            output_dir = safe_child(root, request.output_dir)
            tokenizer = safe_child(root, request.tokenizer, must_exist=True)
            input_dir.relative_to((root / "data").resolve())
            output_dir.relative_to((root / "data").resolve())
            tokenizer.relative_to((root / "artifacts").resolve())
            args = [
                "-m",
                "godot_coder.prepare_data",
                "--input",
                str(input_dir),
                "--output",
                str(output_dir),
                "--tokenizer",
                str(tokenizer),
                "--val-ratio",
                str(request.val_ratio),
            ]
            return app.state.jobs.start("prepare-data", args)
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            raise HTTPException(status_code=400 if not isinstance(exc, RuntimeError) else 409, detail=str(exc)) from exc

    @app.get("/api/jobs/current")
    async def current_job() -> dict[str, Any] | None:
        return app.state.jobs.current()

    @app.get("/api/jobs/history")
    async def job_history() -> list[dict[str, Any]]:
        return app.state.jobs.history()

    @app.get("/api/jobs/{job_id}/export")
    async def export_job_log(job_id: str, format: str = Query(default="text", pattern="^(text|jsonl)$")) -> FileResponse:
        try:
            path = await asyncio.to_thread(app.state.jobs.export_path, job_id, format)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404 if isinstance(exc, FileNotFoundError) else 400, detail=str(exc)) from exc
        suffix = "txt" if format == "text" else "jsonl"
        media_type = "text/plain; charset=utf-8" if format == "text" else "application/x-ndjson"
        return FileResponse(path, media_type=media_type, filename=f"godot-coder-job-{job_id}.{suffix}")

    @app.post("/api/jobs/stop")
    async def stop_job() -> dict[str, Any] | None:
        return await asyncio.to_thread(app.state.jobs.stop)

    @app.get("/api/config/raw")
    async def raw_config(path: str = Query(..., min_length=1)) -> dict[str, str]:
        try:
            config_path = safe_child(root, path, must_exist=True)
            config_path.relative_to((root / "configs").resolve())
            return {"path": path, "content": config_path.read_text(encoding="utf-8")}
        except (ValueError, FileNotFoundError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/api/config/raw")
    async def save_raw_config(request: FileWriteRequest) -> dict[str, Any]:
        try:
            config_path = safe_child(root, request.path)
            config_path.relative_to((root / "configs").resolve())
            if config_path.suffix.lower() not in {".yaml", ".yml"}:
                raise ValueError("training configs must be YAML files")
            parsed = yaml.safe_load(request.content)
            if not isinstance(parsed, dict) or "model" not in parsed or "train" not in parsed:
                raise ValueError("config must contain model and train mappings")
            ModelConfig(**parsed["model"]).validate()
            TrainConfig.from_mapping(parsed["train"]).validate()
            config_path.parent.mkdir(parents=True, exist_ok=True)
            backup = None
            if config_path.exists():
                backup_dir = root / ".studio_backups" / "configs"
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup = backup_dir / f"{config_path.stem}.{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000_000:09d}{config_path.suffix}.bak"
                shutil.copy2(config_path, backup)
            temporary = config_path.with_suffix(config_path.suffix + ".tmp")
            temporary.write_text(request.content, encoding="utf-8", newline="\n")
            os.replace(temporary, config_path)
            return {
                "path": request.path,
                "saved": True,
                "backup": relative_posix(backup, root) if backup else None,
            }
        except (ValueError, OSError, TypeError, yaml.YAMLError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/project-summary")
    async def project_summary() -> dict[str, Any]:
        return {
            "root": str(root),
            "configs": list_configs(root),
            "checkpoints": list_checkpoints(root),
            "manifest": read_manifest(root),
            "jobs": app.state.jobs.history(),
        }

    return app
