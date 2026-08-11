from __future__ import annotations

import asyncio
import json
import queue
import threading
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
                top_p=request.top_p,
                repetition_penalty=request.repetition_penalty,
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
            # The model samples on a worker thread so a slow forward pass
            # never blocks the event loop (job progress, other API calls).
            # queue.Queue is thread-safe; asyncio.to_thread wakes this
            # coroutine once per token without copying data.
            events: queue.Queue[dict[str, Any] | None] = queue.Queue()
            stop = threading.Event()

            def worker() -> None:
                try:
                    for event in app.state.generation.generate_stream(
                        request.checkpoint,
                        request.prompt,
                        max_new_tokens=request.max_new_tokens,
                        temperature=request.temperature,
                        top_k=request.top_k,
                        top_p=request.top_p,
                        repetition_penalty=request.repetition_penalty,
                        device_name=request.device,
                    ):
                        # A disconnected client must not leave the model
                        # generating (and holding the generation lock) until
                        # max_new_tokens: finish the current token, then stop.
                        if stop.is_set():
                            break
                        events.put(event)
                except Exception as exc:
                    events.put({"error": str(exc)})
                finally:
                    events.put(None)  # end-of-stream sentinel

            threading.Thread(target=worker, daemon=True).start()
            try:
                while True:
                    event = await asyncio.to_thread(events.get)
                    if event is None:
                        break
                    if event.get("error"):
                        yield f"data: {json.dumps({'error': event['error']})}\n"
                        break
                    yield f"data: {json.dumps(event)}\n"
            except asyncio.CancelledError:
                stop.set()
                raise
            finally:
                stop.set()
            yield "data: [DONE]\n"

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
