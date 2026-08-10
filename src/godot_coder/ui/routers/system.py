from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from typing import Any

import yaml
from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from ...config import ModelConfig, TrainConfig
from ..paths import safe_child
from ..schemas import FileWriteRequest
from ..services import (
    build_data_catalog,
    delete_dataset_file,
    list_checkpoints,
    list_configs,
    list_dataset_files,
    list_training_reports,
    read_dataset_file,
    read_manifest,
    read_vram_probe,
    relative_posix,
    system_status,
    write_dataset_file,
)


def build_system_router(app: FastAPI) -> APIRouter:
    root = app.state.project_root
    router = APIRouter(tags=["system"])

    @router.get("/api/overview")
    async def overview() -> dict[str, Any]:
        base = await asyncio.to_thread(system_status, root)
        # Bundle frequently-requested data to reduce frontend round-trips
        base["checkpoints"] = await asyncio.to_thread(list_checkpoints, root)
        base["configs"] = await asyncio.to_thread(list_configs, root)
        base["latest_training_reports"] = await asyncio.to_thread(list_training_reports, root)
        return base

    @router.get("/api/configs")
    async def configs() -> list[dict[str, Any]]:
        return await asyncio.to_thread(list_configs, root)

    @router.get("/api/checkpoints")
    async def checkpoints() -> list[dict[str, Any]]:
        return await asyncio.to_thread(list_checkpoints, root)

    @router.get("/api/hardware/probe")
    async def hardware_probe() -> dict[str, Any] | None:
        return await asyncio.to_thread(read_vram_probe, root)

    @router.get("/api/hardware/autotune")
    async def hardware_autotune() -> dict[str, Any] | None:
        path = root / "reports" / "hardware" / "autotune_latest.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None

    @router.get("/api/data/files")
    async def data_files() -> list[dict[str, Any]]:
        return await asyncio.to_thread(list_dataset_files, root)

    @router.get("/api/data/catalog")
    async def data_catalog() -> dict[str, Any]:
        return await asyncio.to_thread(build_data_catalog, root)

    @router.get("/api/data/file")
    async def data_file(path: str = Query(..., min_length=1)) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(read_dataset_file, root, path)
        except (ValueError, FileNotFoundError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.put("/api/data/file")
    async def save_data_file(request: FileWriteRequest) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(write_dataset_file, root, request.path, request.content)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/api/data/file")
    async def remove_data_file(path: str = Query(..., min_length=1)) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(delete_dataset_file, root, path)
        except (ValueError, FileNotFoundError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/data/manifest")
    async def manifest() -> dict[str, Any] | None:
        return await asyncio.to_thread(read_manifest, root)

    @router.get("/api/jobs/current")
    async def current_job() -> dict[str, Any] | None:
        return app.state.jobs.current()

    @router.get("/api/jobs/history")
    async def job_history() -> list[dict[str, Any]]:
        return app.state.jobs.history()

    @router.get("/api/jobs/{job_id}/export")
    async def export_job_log(job_id: str, format: str = Query(default="text", pattern="^(text|jsonl)$")) -> FileResponse:
        try:
            path = await asyncio.to_thread(app.state.jobs.export_path, job_id, format)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404 if isinstance(exc, FileNotFoundError) else 400, detail=str(exc)) from exc
        suffix = "txt" if format == "text" else "jsonl"
        media_type = "text/plain; charset=utf-8" if format == "text" else "application/x-ndjson"
        return FileResponse(path, media_type=media_type, filename=f"godot-coder-job-{job_id}.{suffix}")

    @router.post("/api/jobs/stop")
    async def stop_job() -> dict[str, Any] | None:
        return await asyncio.to_thread(app.state.jobs.stop)

    @router.get("/api/config/raw")
    async def raw_config(path: str = Query(..., min_length=1)) -> dict[str, str]:
        try:
            config_path = safe_child(root, path, must_exist=True)
            config_path.relative_to((root / "configs").resolve())
            return {"path": path, "content": config_path.read_text(encoding="utf-8")}
        except (ValueError, FileNotFoundError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.put("/api/config/raw")
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

    @router.get("/api/project-summary")
    async def project_summary() -> dict[str, Any]:
        return {
            "root": str(root),
            "configs": list_configs(root),
            "checkpoints": list_checkpoints(root),
            "manifest": read_manifest(root),
            "jobs": app.state.jobs.history(),
        }

    return router
