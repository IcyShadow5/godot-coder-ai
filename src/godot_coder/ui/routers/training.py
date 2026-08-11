from __future__ import annotations

import asyncio
from typing import Any

import yaml
from fastapi import APIRouter, FastAPI, HTTPException

from ...checkpoint import load_checkpoint
from ...config import load_config
from ...corpus_audit import build_preflight
from ..paths import safe_child
from ..schemas import BenchmarkRequest, PrepareRequest, TrainRequest, VerifyRequest
from ..services import list_training_reports, read_curriculum_status, relative_posix


def build_training_router(app: FastAPI) -> APIRouter:
    root = app.state.project_root
    router = APIRouter(tags=["training"])

    @router.get("/api/training/reports")
    async def training_reports() -> list[dict[str, Any]]:
        return await asyncio.to_thread(list_training_reports, root)

    @router.get("/api/curriculum/status")
    async def curriculum_status() -> dict[str, Any]:
        return await asyncio.to_thread(read_curriculum_status, root)

    @router.post("/api/jobs/hardware/probe")
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

    @router.post("/api/jobs/hardware/autotune")
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

    @router.post("/api/jobs/train-smoke")
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

    @router.post("/api/jobs/train")
    async def start_training(request: TrainRequest) -> dict[str, Any]:
        try:
            config_path = safe_child(root, request.config, must_exist=True)
            config_path.relative_to((root / "configs").resolve())
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            load_config(config_path)
            readiness = await asyncio.to_thread(build_preflight, root, config_path=config_path, mode="full")
            if not readiness.get("can_start"):
                raise ValueError("Training blocked: " + " · ".join(readiness.get("blockers") or ["Preflight failed"]))
            # max_steps may be null in the config (runs are then driven by
            # target_dataset_passes) - treat null/absent as 0 before int().
            max_steps = int(raw.get("train", {}).get("max_steps") or 0) or None
            await asyncio.to_thread(app.state.generation.unload)
            args = ["-m", "godot_coder.train", "--config", str(config_path)]
            initial_progress: dict[str, Any] | None = None
            if request.resume:
                resume = safe_child(root, request.resume, must_exist=True)
                resume.relative_to((root / "checkpoints").resolve())
                args.extend(["--resume", str(resume)])
                initial_progress = {"resumed_from": relative_posix(resume, root)}
                try:
                    peek = load_checkpoint(resume, map_location="cpu")
                    initial_progress["resume_step"] = int(peek.get("step") or 0)
                except Exception:
                    pass  # A failed peek must not block resuming the run
            return app.state.jobs.start("training", args, max_steps=max_steps, initial_progress=initial_progress)
        except (ValueError, FileNotFoundError, RuntimeError, yaml.YAMLError) as exc:
            raise HTTPException(status_code=400 if not isinstance(exc, RuntimeError) else 409, detail=str(exc)) from exc

    @router.post("/api/jobs/curriculum/build")
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

    @router.post("/api/jobs/curriculum/validate")
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

    @router.post("/api/jobs/curriculum/prepare")
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

    @router.post("/api/jobs/verify")
    async def start_verify(request: VerifyRequest) -> dict[str, Any]:
        try:
            # The verifier never writes inside the project tree and creates
            # its own system-wide temp dir (owned, removed after the run).
            # Handing a --work-dir over here would leak one temp dir per
            # Studio verify job - caller-owned dirs are left in place.
            await asyncio.to_thread(app.state.generation.unload)
            a = safe_child(root, request.checkpoint_a, must_exist=True)
            a.relative_to((root / "checkpoints").resolve())
            if request.checkpoint_b:
                b = safe_child(root, request.checkpoint_b, must_exist=True)
                b.relative_to((root / "checkpoints").resolve())
                b_arg = ["--checkpoint-b", relative_posix(b, root)]
            else:
                b_arg = []
            args = [
                "-m",
                "godot_coder.verify",
                "--project-root",
                str(root),
                "--checkpoint-a",
                relative_posix(a, root),
                *b_arg,
                "--max-new-tokens",
                str(request.max_new_tokens),
            ]
            return app.state.jobs.start("verify", args, max_steps=16)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/api/jobs/benchmark")
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

    @router.post("/api/jobs/prepare")
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

    return router
