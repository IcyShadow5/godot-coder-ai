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


def _sampling_payload(request: GenerateRequest) -> dict[str, object]:
    """The knobs that shaped this completion, for the history record."""
    return {
        "max_new_tokens": request.max_new_tokens,
        "temperature": request.temperature,
        "top_k": request.top_k,
        "top_p": request.top_p,
        "repetition_penalty": request.repetition_penalty,
        "task_format": request.task_format,
        "strict_context": request.strict_context,
    }


def build_chat_router(app: FastAPI) -> APIRouter:
    root = app.state.project_root
    router = APIRouter(tags=["chat"])

    @router.get("/api/chat/sessions")
    def list_sessions() -> dict[str, Any]:
        return {"sessions": app.state.chat_store.list_sessions()}

    @router.get("/api/chat/sessions/{session_id}")
    def session_messages(session_id: str) -> dict[str, Any]:
        try:
            return {"session_id": session_id, "messages": app.state.chat_store.load(session_id)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/api/chat/sessions/{session_id}")
    def delete_session(session_id: str) -> dict[str, Any]:
        try:
            return {"deleted": app.state.chat_store.delete(session_id)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/chat/generate")
    async def generate(request: GenerateRequest) -> dict[str, Any]:
        current = app.state.jobs.current()
        if current and current["status"] in {"starting", "running", "stopping"}:
            raise HTTPException(status_code=409, detail="Stop the active training/data job before generating.")
        try:
            result = await asyncio.to_thread(
                app.state.generation.generate,
                request.checkpoint,
                request.prompt,
                max_new_tokens=request.max_new_tokens,
                temperature=request.temperature,
                top_k=request.top_k,
                top_p=request.top_p,
                repetition_penalty=request.repetition_penalty,
                device_name=request.device,
                task_format=request.task_format,
                strict_context=request.strict_context,
            )
            # cancelled distinguishes an interrupted (partial) completion
            # from a finished one; the partial text is still returned.
            return {
                "text": result.text,
                "cancelled": result.cancelled,
                "checkpoint": request.checkpoint,
            }
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/chat/generate-stream")
    async def generate_stream(request: GenerateRequest):
        current = app.state.jobs.current()
        if current and current["status"] in {"starting", "running", "stopping"}:
            raise HTTPException(status_code=409, detail="Stop the active training/data job before generating.")
        # The user turn is persisted up front so a later stream failure
        # never loses the request itself.
        if request.session_id:
            try:
                await asyncio.to_thread(
                    app.state.chat_store.append,
                    request.session_id,
                    "user",
                    request.prompt,
                    checkpoint=request.checkpoint,
                    sampling=_sampling_payload(request),
                )
            except Exception:
                pass  # history must never block the generation itself

        async def token_stream():
            # The model samples on a worker thread so a slow forward pass
            # never blocks the event loop (job progress, other API calls).
            # queue.Queue is thread-safe; asyncio.to_thread wakes this
            # coroutine once per token without copying data.
            events: queue.Queue[dict[str, Any] | None] = queue.Queue()
            stop = threading.Event()

            def worker() -> None:
                collected: list[str] = []
                context_report: dict[str, object] | None = None
                done_text: str | None = None
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
                        task_format=request.task_format,
                        strict_context=request.strict_context,
                    ):
                        # A disconnected client must not leave the model
                        # generating (and holding the generation lock) until
                        # max_new_tokens: finish the current token, then stop.
                        if stop.is_set():
                            break
                        if event.get("context"):
                            context_report = event["context"]
                        if event.get("token"):
                            collected.append(str(event["token"]))
                        if event.get("done") and event.get("text"):
                            done_text = str(event["text"])
                        events.put(event)
                except Exception as exc:
                    events.put({"error": str(exc)})
                finally:
                    # Persist the assistant turn (also on disconnect: the
                    # partial completion is still worth keeping). This runs
                    # before the sentinel so the client sees the persisted
                    # line when it receives the final [DONE] event.
                    if request.session_id:
                        try:
                            app.state.chat_store.append(
                                request.session_id,
                                "assistant",
                                done_text if done_text is not None else "".join(collected),
                                checkpoint=request.checkpoint,
                                sampling=_sampling_payload(request),
                                context=context_report,
                            )
                        except Exception:
                            pass  # history must never break the stream
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
            result = await asyncio.to_thread(validate_code, root, request.code, request.project)
            if request.session_id:
                try:
                    app.state.chat_store.attach_validation(
                        request.session_id,
                        {
                            "passed": bool(result.get("passed")),
                            "timed_out": bool(result.get("timed_out")),
                        },
                    )
                except Exception:
                    pass  # history must never break the check itself
            return result
        except (ValueError, FileNotFoundError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
