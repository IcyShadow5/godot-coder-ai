from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query

from ...corpus import load_registry, save_registry, status as corpus_status
from ...corpus_audit import build_preflight
from ...local_sources import open_inbox as open_local_inbox, status as local_source_status
from ..paths import safe_child
from ..schemas import CorpusSourceRequest, CorpusTokenizerRequest, LocalSourceImportRequest, _local_import_extra_env


def build_corpus_router(app: FastAPI) -> APIRouter:
    root = app.state.project_root
    router = APIRouter(tags=["corpus"])

    @router.get("/api/corpus/status")
    async def get_corpus_status() -> dict[str, Any]:
        return await asyncio.to_thread(corpus_status, root)

    @router.get("/api/corpus/local")
    async def get_local_sources() -> dict[str, Any]:
        return await asyncio.to_thread(local_source_status, root)

    @router.post("/api/corpus/local/open")
    async def open_local_sources_inbox() -> dict[str, Any]:
        try:
            await asyncio.to_thread(open_local_inbox, root)
            return {"opened": True, "path": str(root / "data" / "local_sources" / "inbox")}
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/api/jobs/corpus/local-import")
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

    @router.get("/api/preflight")
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

    @router.put("/api/corpus/sources")
    async def update_corpus_sources(request: CorpusSourceRequest) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(save_registry, root, request.sources)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/jobs/corpus/fetch")
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

    @router.post("/api/jobs/corpus/build")
    async def corpus_build() -> dict[str, Any]:
        try:
            return app.state.jobs.start(
                "corpus-scan",
                ["-m", "godot_coder.corpus", "--root", str(root), "build"],
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/api/jobs/corpus/validate")
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

    @router.post("/api/jobs/corpus/audit")
    async def corpus_audit() -> dict[str, Any]:
        try:
            return app.state.jobs.start(
                "corpus-audit",
                ["-m", "godot_coder.corpus_audit", "--root", str(root), "audit"],
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/api/jobs/corpus/tokenizer")
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

    @router.post("/api/jobs/corpus/instructions")
    async def corpus_instructions() -> dict[str, Any]:
        try:
            return app.state.jobs.start(
                "instruction-seed-build",
                ["-m", "godot_coder.instruction_data", "--root", str(root), "build"],
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/api/corpus/scale-plan")
    async def corpus_scale_plan() -> dict[str, Any]:
        from ..scale_plan import build_scale_plan
        return await asyncio.to_thread(build_scale_plan, root)

    @router.post("/api/jobs/corpus/prepare")
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

    return router
