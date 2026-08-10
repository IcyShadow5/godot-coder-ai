from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from ..schemas import GenerateRequest, ValidateRequest
from ..services import validate_code


def build_chat_router(app: FastAPI) -> APIRouter:
    root = app.state.project_root
    router = APIRouter(tags=["chat"])

    @router.post("/api/chat/generate")
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

    @router.post("/api/chat/generate-stream")
    async def generate_stream(request: GenerateRequest):
        current = app.state.jobs.current()
        if current and current["status"] in {"starting", "running", "stopping"}:
            raise HTTPException(status_code=409, detail="Stop the active training/data job before generating.")

        async def token_stream():
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

    @router.post("/api/chat/validate")
    async def validate(request: ValidateRequest) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(validate_code, root, request.code, request.project)
        except (ValueError, FileNotFoundError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
