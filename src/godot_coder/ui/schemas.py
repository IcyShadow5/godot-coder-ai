from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..sampling import DEFAULT_REPETITION_PENALTY, DEFAULT_TEMPERATURE, DEFAULT_TOP_K, DEFAULT_TOP_P


# Request models and the one shared helper used by the ui route groups.
# Kept out of server.py so the routers never need to import the app module.


class GenerateRequest(BaseModel):
    checkpoint: str
    prompt: str
    max_new_tokens: int = Field(default=300, ge=1, le=4096)
    # Interactive chat defaults live in one place (sampling.py) so the
    # Studio and the CLI agree; each request can still override them.
    temperature: float = Field(default=DEFAULT_TEMPERATURE, ge=0.0, le=5.0)
    top_k: int = Field(default=DEFAULT_TOP_K, ge=0, le=1000)
    top_p: float = Field(default=DEFAULT_TOP_P, gt=0.0, le=1.0)
    repetition_penalty: float = Field(default=DEFAULT_REPETITION_PENALTY, ge=1.0, le=2.0)
    device: str = "auto"
    # The server composes the prompt from named parts; task_format wraps
    # the request in the training header format (older clients already
    # wrap client-side, so the default stays off).
    task_format: bool = False
    # Refuse calls whose context would exceed the window instead of trimming.
    strict_context: bool = False
    # When set, the turn is persisted into this chat session (JSONL under
    # reports/chat/). The session is created lazily on first use, so an
    # empty draft never touches the disk.
    session_id: str | None = None


class ValidateRequest(BaseModel):
    code: str
    project: str = "data/raw/seed_project"
    # When set, the check result is attached to the last assistant
    # message of this session so the history shows whether the
    # completion parsed (badges on reload).
    session_id: str | None = None


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
