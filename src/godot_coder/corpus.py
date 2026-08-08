from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import time
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import urlparse

from . import __version__
from .godot_cli import build_check_command, build_project_script_command, build_project_validation_command
from .process_control import run_managed_process
from .progress_events import serialize_event
from .tokenizer import BPETokenizer

CORPUS_FORMAT_VERSION = 3
ALLOWED_LICENSES = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "CC-BY-3.0", "LicenseRef-User-Owned-Private"}
EXCLUDED_PARTS = {
    ".git", ".godot", ".import", "node_modules", "thirdparty", "third_party", "vendor",
    "build", "dist", "bin", "obj", "coverage", "__pycache__", "addons",
}

OFFICIAL_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "id": "godot-demo-projects",
        "title": "Official Godot Demo Projects",
        "description": "Many complete 2D, 3D, UI, network and physics examples with project.godot.",
        "url": "https://github.com/godotengine/godot-demo-projects.git",
        "branch": "4.7-6ad6167",
        "kind": "godot_projects",
        "license": "MIT",
        "license_scope": "source-code",
        "attribution": "Godot Engine contributors",
        "catalog_tier": "official",
        "verified": True,
        "verification_url": "https://github.com/godotengine/godot-demo-projects/blob/master/LICENSE.md",
        "enabled": True,
        "beginner_recommended": True,
    },
    {
        "id": "godot-docs",
        "title": "Official Godot Documentation",
        "description": "GDScript code snippets from the official documentation; useful for APIs and small examples.",
        "url": "https://github.com/godotengine/godot-docs.git",
        "branch": "4.7",
        "kind": "rst_gdscript",
        "license": "CC-BY-3.0",
        "license_scope": "documentation-examples",
        "attribution": "Juan Linietsky, Ariel Manzur and the Godot community",
        "catalog_tier": "official",
        "verified": True,
        "verification_url": "https://github.com/godotengine/godot-docs/blob/master/LICENSE.txt",
        "exclude_paths": ["classes"],
        "enabled": True,
        "beginner_recommended": True,
    },
    {
        "id": "gdquest-learn-gdscript",
        "title": "GDQuest · Learn GDScript From Zero",
        "description": "Large, structured Godot learning project with many real GDScript files. MIT source code; media is not read in.",
        "url": "https://github.com/GDQuest/learn-gdscript.git",
        "branch": "main",
        "kind": "godot_projects",
        "license": "MIT",
        "license_scope": "source-code-only",
        "attribution": "GDQuest and contributors",
        "catalog_tier": "verified-community",
        "verified": True,
        "verification_url": "https://github.com/GDQuest/learn-gdscript/blob/main/LICENSE",
        "exclude_paths": ["addons", "html_export", "i18n"],
        "enabled": False,
        "beginner_recommended": True,
    },
    {
        "id": "gdquest-getting-started-godot4",
        "title": "GDQuest · Getting Started with Godot 4",
        "description": "Two complete, beginner-friendly Godot 4 projects with MIT-licensed source code.",
        "url": "https://github.com/gdquest-demos/getting-started-with-godot-4.git",
        "branch": "main",
        "kind": "godot_projects",
        "license": "MIT",
        "license_scope": "source-code",
        "attribution": "GDQuest and contributors",
        "catalog_tier": "verified-community",
        "verified": True,
        "verification_url": "https://github.com/gdquest-demos/getting-started-with-godot-4/blob/main/LICENSE",
        "enabled": False,
        "beginner_recommended": True,
    },
    {
        "id": "gdquest-open-rpg",
        "title": "GDQuest · Godot Open RPG",
        "description": "Larger Godot 4 RPG project with a coherent architecture and MIT-licensed GDScript.",
        "url": "https://github.com/gdquest-demos/godot-open-rpg.git",
        "branch": "main",
        "kind": "godot_projects",
        "license": "MIT",
        "license_scope": "source-code",
        "attribution": "GDQuest and contributors",
        "catalog_tier": "verified-community",
        "verified": True,
        "verification_url": "https://github.com/gdquest-demos/godot-open-rpg/blob/main/LICENSE",
        "enabled": False,
        "beginner_recommended": True,
    },
    {
        "id": "gdquest-godot4-new-features",
        "title": "GDQuest · Godot 4 New Features",
        "description": "Several Godot 4 demo projects covering new engine features. Only MIT-licensed source code is read in; media stays excluded.",
        "url": "https://github.com/gdquest-demos/godot-4-new-features.git",
        "branch": "main",
        "kind": "godot_projects",
        "license": "MIT",
        "license_scope": "source-code-only",
        "attribution": "GDQuest and contributors",
        "catalog_tier": "verified-community",
        "verified": True,
        "verification_url": "https://github.com/gdquest-demos/godot-4-new-features/blob/main/LICENSE",
        "enabled": False,
        "beginner_recommended": True,
    },
    {
        "id": "gdquest-third-person-controller",
        "title": "GDQuest · 3D Third-Person Controller",
        "description": "A coherent Godot 4 controller project with movement, camera and state logic. Only MIT-licensed source code is read in.",
        "url": "https://github.com/gdquest-demos/godot-4-3d-third-person-controller.git",
        "branch": "main",
        "kind": "godot_projects",
        "license": "MIT",
        "license_scope": "source-code-only",
        "attribution": "GDQuest and contributors",
        "catalog_tier": "verified-community",
        "verified": True,
        "verification_url": "https://github.com/gdquest-demos/godot-4-3d-third-person-controller/blob/main/LICENSE",
        "enabled": False,
        "beginner_recommended": True,
    },

    {
        "id": "gdquest-godot4-how-tos",
        "title": "GDQuest · Godot 4 How-Tos",
        "description": "Many focused Godot 4 examples covering UI, 2D, 3D, resources and gameplay.",
        "url": "https://github.com/gdquest-demos/godot-4-how-tos.git",
        "branch": "main", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "GDQuest and contributors",
        "catalog_tier": "verified-community", "expansion_tier": "core-5m", "verified": True,
        "verification_url": "https://github.com/gdquest-demos/godot-4-how-tos/blob/main/LICENSE",
        "estimated_unique_tokens": 350000, "source_only": True, "enabled": False,
    },
    {
        "id": "pixelorama",
        "title": "Pixelorama",
        "description": "Large production Godot 4 application with editor, UI, file, drawing and extension architecture.",
        "url": "https://github.com/Orama-Interactive/Pixelorama.git",
        "branch": "master", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "Orama Interactive and contributors",
        "catalog_tier": "verified-community", "expansion_tier": "core-5m", "verified": True,
        "verification_url": "https://github.com/Orama-Interactive/Pixelorama/blob/master/LICENSE",
        "estimated_unique_tokens": 1500000, "source_only": True, "enabled": False,
    },
    {
        "id": "material-maker",
        "title": "Material Maker",
        "description": "Large Godot application with graph, shader, UI, export and project logic. Add-on copies stay excluded.",
        "url": "https://github.com/RodZill4/material-maker.git",
        "branch": "master", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "RodZill4 and contributors",
        "catalog_tier": "verified-community", "expansion_tier": "core-5m", "verified": True,
        "verification_url": "https://github.com/RodZill4/material-maker/blob/master/LICENSE.md",
        "exclude_paths": ["addons"], "estimated_unique_tokens": 1400000, "source_only": True, "enabled": False,
    },
    {
        "id": "dialogic",
        "title": "Dialogic",
        "description": "Comprehensive Godot 4 dialog system with editor plugin, runtime, timeline and event logic.",
        "url": "https://github.com/dialogic-godot/dialogic.git",
        "branch": "main", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "Dialogic contributors",
        "catalog_tier": "verified-community", "expansion_tier": "core-5m", "verified": True,
        "verification_url": "https://github.com/dialogic-godot/dialogic/blob/main/LICENSE",
        "estimated_unique_tokens": 900000, "source_only": True, "allow_addons": True, "enabled": False,
    },
    {
        "id": "dialogue-manager",
        "title": "Dialogue Manager",
        "description": "Production Godot 4 dialog add-on with parser, editor, import and runtime code.",
        "url": "https://github.com/nathanhoad/godot_dialogue_manager.git",
        "branch": "main", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "Nathan Hoad and contributors",
        "catalog_tier": "verified-community", "expansion_tier": "core-5m", "verified": True,
        "verification_url": "https://github.com/nathanhoad/godot_dialogue_manager/blob/main/LICENSE",
        "estimated_unique_tokens": 450000, "source_only": True, "allow_addons": True, "enabled": False,
    },
    {
        "id": "beehave",
        "title": "Beehave · Behavior Trees",
        "description": "Godot 4 behavior trees; blackboard, debug, editor and AI examples.",
        "url": "https://github.com/bitbrain/beehave.git",
        "branch": "godot-4.x", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "bitbrain and contributors",
        "catalog_tier": "verified-community", "expansion_tier": "core-5m", "verified": True,
        "verification_url": "https://github.com/bitbrain/beehave/blob/godot-4.x/LICENSE",
        "estimated_unique_tokens": 300000, "source_only": True, "allow_addons": True, "enabled": False,
    },
    {
        "id": "godot-xr-tools",
        "title": "Godot XR Tools",
        "description": "Comprehensive Godot 4 XR interaction, movement, UI and tooling logic.",
        "url": "https://github.com/GodotVR/godot-xr-tools.git",
        "branch": "master", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "Godot XR contributors",
        "catalog_tier": "verified-community", "expansion_tier": "core-5m", "verified": True,
        "verification_url": "https://github.com/GodotVR/godot-xr-tools/blob/master/LICENSE",
        "estimated_unique_tokens": 650000, "source_only": True, "allow_addons": True, "enabled": False,
    },
    {
        "id": "godot-heightmap-plugin",
        "title": "Godot Heightmap Terrain",
        "description": "Large Godot 4 terrain tool with editor, import, LOD and runtime logic.",
        "url": "https://github.com/Zylann/godot_heightmap_plugin.git",
        "branch": "master", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "Zylann and contributors",
        "catalog_tier": "verified-community", "expansion_tier": "core-5m", "verified": True,
        "verification_url": "https://github.com/Zylann/godot_heightmap_plugin/blob/master/LICENSE",
        "estimated_unique_tokens": 450000, "source_only": True, "allow_addons": True, "enabled": False,
    },
    {
        "id": "gdunit4",
        "title": "GdUnit4",
        "description": "Comprehensive Godot 4 test framework with runner, assertions, mocking, reports and editor integration.",
        "url": "https://github.com/MikeSchulze/gdUnit4.git",
        "branch": "master", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "Mike Schulze and contributors",
        "catalog_tier": "verified-community", "expansion_tier": "extended-20m", "verified": True,
        "verification_url": "https://github.com/MikeSchulze/gdUnit4/blob/master/LICENSE",
        "estimated_unique_tokens": 850000, "source_only": True, "allow_addons": True, "enabled": False,
    },
    {
        "id": "godot-statecharts",
        "title": "Godot Statecharts",
        "description": "Hierarchical and parallel state machines for Godot 4 with editor and runtime code.",
        "url": "https://github.com/derkork/godot-statecharts.git",
        "branch": "main", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "derkork and contributors",
        "catalog_tier": "verified-community", "expansion_tier": "extended-20m", "verified": True,
        "verification_url": "https://github.com/derkork/godot-statecharts/blob/main/LICENSE",
        "estimated_unique_tokens": 350000, "source_only": True, "allow_addons": True, "enabled": False,
    },
    {
        "id": "phantom-camera",
        "title": "Phantom Camera",
        "description": "Godot 4 camera system with 2D/3D tracking, tweening, priorities and editor tools.",
        "url": "https://github.com/ramokz/phantom-camera.git",
        "branch": "main", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "Phantom Camera contributors",
        "catalog_tier": "verified-community", "expansion_tier": "extended-20m", "verified": True,
        "verification_url": "https://github.com/ramokz/phantom-camera/blob/main/LICENSE",
        "estimated_unique_tokens": 300000, "source_only": True, "allow_addons": True, "enabled": False,
    },
    {
        "id": "escoria",
        "title": "Escoria",
        "description": "Adventure game framework with inventory, dialog, commands, savegames and editor tools.",
        "url": "https://github.com/godot-escoria/escoria.git",
        "branch": "main", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "Escoria contributors",
        "catalog_tier": "verified-community", "expansion_tier": "extended-20m", "verified": True,
        "verification_url": "https://github.com/godot-escoria/escoria/blob/main/LICENSE",
        "estimated_unique_tokens": 700000, "source_only": True, "allow_addons": True, "enabled": False,
    },
    {
        "id": "godot-next",
        "title": "Godot Next",
        "description": "Additional Godot components and reusable GDScript helpers.",
        "url": "https://github.com/godot-extended-libraries/godot-next.git",
        "branch": "master", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "Godot Extended Libraries contributors",
        "catalog_tier": "verified-community", "expansion_tier": "extended-20m", "verified": True,
        "verification_url": "https://github.com/godot-extended-libraries/godot-next/blob/master/LICENSE",
        "estimated_unique_tokens": 180000, "source_only": True, "allow_addons": True, "enabled": False,
    },
    {
        "id": "godot-mod-loader",
        "title": "Godot Mod Loader",
        "description": "Godot 4 mod loading, hook, configuration and integration logic.",
        "url": "https://github.com/GodotModding/godot-mod-loader.git",
        "branch": "4.x-dev", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "Godot Modding contributors",
        "catalog_tier": "verified-community", "expansion_tier": "extended-20m", "verified": True,
        "verification_url": "https://github.com/GodotModding/godot-mod-loader/blob/4.x-dev/LICENSE",
        "estimated_unique_tokens": 220000, "source_only": True, "allow_addons": True, "enabled": False,
    },
    {
        "id": "maaack-game-template",
        "title": "Maaack · Game Template",
        "description": "Godot 4 game scaffold with menus, settings, pause, save and scene flow.",
        "url": "https://github.com/Maaack/Godot-Game-Template.git",
        "branch": "main", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "Maaack and contributors",
        "catalog_tier": "verified-community", "expansion_tier": "extended-20m", "verified": True,
        "verification_url": "https://github.com/Maaack/Godot-Game-Template/blob/main/LICENSE",
        "estimated_unique_tokens": 220000, "source_only": True, "allow_addons": True, "enabled": False,
    },
    {
        "id": "maaack-menus-template",
        "title": "Maaack · Menus Template",
        "description": "Godot 4 menu, options, input and navigation systems.",
        "url": "https://github.com/Maaack/Godot-Menus-Template.git",
        "branch": "main", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "Maaack and contributors",
        "catalog_tier": "verified-community", "expansion_tier": "extended-20m", "verified": True,
        "verification_url": "https://github.com/Maaack/Godot-Menus-Template/blob/main/LICENSE",
        "estimated_unique_tokens": 180000, "source_only": True, "allow_addons": True, "enabled": False,
    },
    {
        "id": "godot-input-helper",
        "title": "Godot Input Helper",
        "description": "Godot 4 input abstraction, device switching, glyphs and rebinding.",
        "url": "https://github.com/nathanhoad/godot_input_helper.git",
        "branch": "main", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "Nathan Hoad and contributors",
        "catalog_tier": "verified-community", "expansion_tier": "extended-20m", "verified": True,
        "verification_url": "https://github.com/nathanhoad/godot_input_helper/blob/main/LICENSE",
        "estimated_unique_tokens": 160000, "source_only": True, "allow_addons": True, "enabled": False,
    },
    {
        "id": "godot-sound-manager",
        "title": "Godot Sound Manager",
        "description": "Godot 4 audio, bus, music, transition and pooling logic.",
        "url": "https://github.com/nathanhoad/godot_sound_manager.git",
        "branch": "main", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "Nathan Hoad and contributors",
        "catalog_tier": "verified-community", "expansion_tier": "extended-20m", "verified": True,
        "verification_url": "https://github.com/nathanhoad/godot_sound_manager/blob/main/LICENSE",
        "estimated_unique_tokens": 130000, "source_only": True, "allow_addons": True, "enabled": False,
    },
    {
        "id": "scatter",
        "title": "ProtonScatter",
        "description": "Godot 4 procedural distribution with modifiers, editor tools and runtime integration.",
        "url": "https://github.com/HungryProton/scatter.git",
        "branch": "main", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "HungryProton and contributors",
        "catalog_tier": "verified-community", "expansion_tier": "extended-20m", "verified": True,
        "verification_url": "https://github.com/HungryProton/scatter/blob/main/LICENSE",
        "estimated_unique_tokens": 350000, "source_only": True, "allow_addons": True, "enabled": False,
    },
    {
        "id": "tabletop-club",
        "title": "Tabletop Club",
        "description": "Large Godot 4 application with multiplayer, UI, save, workshop, physics and game state logic.",
        "url": "https://github.com/drwhut/tabletop-club.git",
        "branch": "master", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "Benjamin Beddows and Tabletop Club contributors",
        "catalog_tier": "verified-community", "expansion_tier": "extended-20m", "verified": True,
        "verification_url": "https://github.com/drwhut/tabletop-club/blob/master/LICENSE",
        "estimated_unique_tokens": 1800000, "source_only": True, "exclude_paths": ["game/assets"], "enabled": False,
    },
    {
        "id": "godot-rl-agents",
        "title": "Godot RL Agents",
        "description": "Godot 4 agent, sensor, action and training interface examples in GDScript.",
        "url": "https://github.com/edbeeching/godot_rl_agents.git",
        "branch": "main", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "Edward Beeching and contributors",
        "catalog_tier": "verified-community", "expansion_tier": "extended-20m", "verified": True,
        "verification_url": "https://github.com/edbeeching/godot_rl_agents/blob/main/LICENSE",
        "estimated_unique_tokens": 450000, "source_only": True, "allow_addons": True, "enabled": False,
    },
    {
        "id": "godot-open-rts",
        "title": "Godot Open RTS",
        "description": "Godot 4 RTS example with selection, unit control, navigation, camera and UI.",
        "url": "https://github.com/lampe-games/godot-open-rts.git",
        "branch": "main", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "Lampe Games and contributors",
        "catalog_tier": "verified-community", "expansion_tier": "extended-20m", "verified": True,
        "verification_url": "https://github.com/lampe-games/godot-open-rts/blob/main/LICENSE",
        "estimated_unique_tokens": 250000, "source_only": True, "enabled": False,
    },
    {
        "id": "godot-firebase",
        "title": "Godot Firebase",
        "description": "Godot 4 Firebase integration with authentication, database, storage and HTTP logic.",
        "url": "https://github.com/GodotNuts/GodotFirebase.git",
        "branch": "main", "kind": "godot_projects", "license": "MIT",
        "license_scope": "source-code", "attribution": "Kyle Szklenski and Godot Firebase contributors",
        "catalog_tier": "verified-community", "expansion_tier": "extended-20m", "verified": True,
        "verification_url": "https://github.com/GodotNuts/GodotFirebase/blob/main/LICENSE",
        "estimated_unique_tokens": 300000, "source_only": True, "allow_addons": True, "enabled": False,
    },
)

LICENSE_FILENAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "COPYING.md", "COPYING.txt")
LICENSE_MARKERS: dict[str, tuple[str, ...]] = {
    "MIT": ("permission is hereby granted, free of charge", 'the software is provided "as is"'),
    "CC-BY-3.0": ("attribution 3.0",),
    "LicenseRef-User-Owned-Private": ("user-owned-private",),
    "Apache-2.0": ("apache license", "version 2.0"),
    "BSD-2-Clause": ("redistribution and use in source and binary forms", "disclaimer"),
    "BSD-3-Clause": ("redistribution and use in source and binary forms", "neither the name"),
}



@dataclass
class CorpusRecord:
    record_id: str
    source_id: str
    source_title: str
    group_id: str
    kind: str
    original_path: str
    staged_path: str
    split: str
    content_sha256: str
    bytes: int
    license: str
    attribution: str
    source_url: str | None = None
    source_commit: str | None = None
    source_ref: str | None = None
    project_root: str | None = None
    validation_status: str = "pending"
    validation_error: str | None = None


def _json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _run(command: list[str], *, cwd: Path | None = None, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, check=False
    )


def _valid_git_url(url: str) -> bool:
    if url.startswith("git@") and ":" in url:
        return True
    parsed = urlparse(url)
    return parsed.scheme in {"https", "ssh"} and bool(parsed.netloc)


def _directory_size_bytes(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _replace_directory(temporary: Path, destination: Path) -> None:
    backup = destination.with_name(destination.name + ".previous")
    if backup.exists():
        shutil.rmtree(backup)
    if destination.exists():
        destination.replace(backup)
    try:
        temporary.replace(destination)
    except Exception:
        if backup.exists() and not destination.exists():
            backup.replace(destination)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def _stable_fraction(text: str) -> float:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64)


def _split_for_group(group_id: str) -> str:
    fraction = _stable_fraction(group_id)
    if fraction < 0.05:
        return "test"
    if fraction < 0.10:
        return "val"
    return "train"


def _normalize_code(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def _is_excluded(path: Path, source: dict[str, Any] | None = None) -> bool:
    lowered = {part.lower() for part in path.parts}
    excluded = set(EXCLUDED_PARTS)
    if source and source.get("allow_addons"):
        excluded.discard("addons")
    return bool(lowered & excluded) or any(part.startswith(".") for part in path.parts if part not in {".", ".."})


def _source_path_allowed(path: Path, source: dict[str, Any]) -> bool:
    relative = path.as_posix().lstrip("/")
    include_paths = [str(item).replace("\\", "/").strip("/") for item in source.get("include_paths", []) if str(item).strip("/\\")]
    if include_paths and not any(relative == prefix or relative.startswith(prefix + "/") for prefix in include_paths):
        return False
    for prefix in source.get("exclude_paths", []):
        normalized = str(prefix).replace("\\", "/").strip("/")
        if relative == normalized or relative.startswith(normalized + "/"):
            return False
    return True


def corpus_root(project_root: Path) -> Path:
    return project_root / "data" / "corpus"


def registry_path(project_root: Path) -> Path:
    return corpus_root(project_root) / "sources.json"


def load_registry(project_root: Path) -> dict[str, Any]:
    path = registry_path(project_root)
    if not path.exists():
        payload = {"format_version": CORPUS_FORMAT_VERSION, "sources": [dict(item) for item in OFFICIAL_PRESETS]}
        _json_write(path, payload)
        return payload
    payload = json.loads(path.read_text(encoding="utf-8"))
    version = int(payload.get("format_version", 1))
    if version not in {1, 2, CORPUS_FORMAT_VERSION}:
        raise ValueError("unsupported corpus source registry version")
    payload["format_version"] = CORPUS_FORMAT_VERSION

    # Add newly verified catalog entries without changing a user's existing choices.
    existing_by_id = {str(item.get("id")): item for item in payload.get("sources", [])}
    catalog_changed = False
    for preset in OFFICIAL_PRESETS:
        current = existing_by_id.get(preset["id"])
        if current is None:
            added = dict(preset)
            # Existing registries represent explicit user choices. New catalog entries
            # must never start downloading automatically during a version migration.
            added["enabled"] = False
            payload.setdefault("sources", []).append(added)
            catalog_changed = True
            continue
        for key in ("catalog_tier", "verified", "verification_url", "license_scope", "expansion_tier", "estimated_unique_tokens", "source_only", "allow_addons", "include_paths"):
            if key not in current and key in preset:
                current[key] = preset[key]
                catalog_changed = True

    # v0.4.0/0.4.1 shipped a non-existent demo branch named ``4.7``.
    # Godot publishes the matching demos as the immutable tag ``4.7-6ad6167``.
    # Only migrate the exact broken built-in value so user-defined refs remain untouched.
    migrated = False
    for source in payload.get("sources", []):
        if source.get("id") == "godot-demo-projects" and source.get("branch") == "4.7":
            source["branch"] = "4.7-6ad6167"
            migrated = True

    # v0.10.3: heal stale catalog metadata (e.g. pre-anglicization German
    # titles/descriptions) for known catalog entries. User choices such as
    # ``enabled``, ``branch`` or custom refs are never overwritten.
    healed = False
    for preset in OFFICIAL_PRESETS:
        current = existing_by_id.get(preset["id"])
        if current is None:
            continue
        for key in ("title", "description"):
            if preset.get(key) and current.get(key) != preset[key]:
                current[key] = preset[key]
                healed = True

    # v0.10.3: local-* entries written before the anglicization kept the
    # German ``Privat ·`` title and German description template. The import
    # already regenerates these on the next run; this migration simply heals
    # registries that were saved in between. Names and URLs stay untouched.
    for source in payload.get("sources", []):
        if not str(source.get("id", "")).startswith("local-"):
            continue
        title = str(source.get("title", ""))
        if title.startswith("Privat · "):
            source["title"] = "Private · " + title[len("Privat · "):]
            healed = True
        description = str(source.get("description", ""))
        if description == "Lokales, vom Nutzer bestätigtes Godot-Projekt. Nur lokal trainieren; nicht weiterverteilen.":
            source["description"] = (
                "A local Godot project confirmed by the user. "
                "Train locally only; do not redistribute."
            )
            healed = True

    if migrated or healed or catalog_changed or version != CORPUS_FORMAT_VERSION:
        _json_write(path, payload)
    return payload


def save_registry(project_root: Path, sources: list[dict[str, Any]]) -> dict[str, Any]:
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for source in sources:
        source_id = str(source.get("id", "")).strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", source_id):
            raise ValueError(f"invalid source id: {source_id!r}")
        if source_id in seen:
            raise ValueError(f"duplicate source id: {source_id}")
        seen.add(source_id)
        license_name = str(source.get("license", "UNKNOWN")).strip()
        if license_name not in ALLOWED_LICENSES:
            raise ValueError(f"source {source_id} has unsupported or unknown license {license_name!r}")
        kind = str(source.get("kind", "godot_projects"))
        if kind not in {"godot_projects", "rst_gdscript"}:
            raise ValueError(f"unsupported source kind: {kind}")
        url = str(source.get("url") or "").strip()
        is_local = url.startswith("local://")
        if is_local:
            if license_name != "LicenseRef-User-Owned-Private" or not bool(source.get("owner_confirmed")):
                raise ValueError(f"local source {source_id} requires confirmed user ownership")
        elif not _valid_git_url(url):
            raise ValueError(f"source {source_id} must use an HTTPS or SSH Git URL")
        reference = str(source.get("branch") or source.get("ref") or "main").strip()
        if not reference or any(character.isspace() for character in reference):
            raise ValueError(f"source {source_id} has an invalid Git ref")
        normalized.append({
            "id": source_id,
            "title": str(source.get("title") or source_id),
            "description": str(source.get("description") or "Custom source"),
            "url": url,
            "branch": reference,
            "kind": kind,
            "license": license_name,
            "attribution": str(source.get("attribution") or "Unknown contributor"),
            "exclude_paths": [str(item).strip("/\\") for item in source.get("exclude_paths", []) if str(item).strip("/\\")],
            "include_paths": [str(item).strip("/\\") for item in source.get("include_paths", []) if str(item).strip("/\\")],
            "enabled": bool(source.get("enabled", True)),
            "beginner_recommended": bool(source.get("beginner_recommended", False)),
            "catalog_tier": str(source.get("catalog_tier") or "custom"),
            "verified": bool(source.get("verified", False)),
            "verification_url": str(source.get("verification_url") or ""),
            "license_scope": str(source.get("license_scope") or "source-code"),
            "expansion_tier": str(source.get("expansion_tier") or ""),
            "estimated_unique_tokens": max(0, int(source.get("estimated_unique_tokens") or 0)),
            "source_only": bool(source.get("source_only", False)),
            "allow_addons": bool(source.get("allow_addons", False)),
            "owner_confirmed": bool(source.get("owner_confirmed", False)),
            "redistribution_allowed": bool(source.get("redistribution_allowed", True)),
            "split_policy": str(source.get("split_policy") or "grouped"),
        })
    payload = {"format_version": CORPUS_FORMAT_VERSION, "sources": normalized}
    _json_write(registry_path(project_root), payload)
    return payload


def _git_available() -> bool:
    return shutil.which("git") is not None


def _git_commit(repo: Path) -> str | None:
    result = _run(["git", "rev-parse", "HEAD"], cwd=repo, timeout=20)
    return result.stdout.strip() if result.returncode == 0 else None


def _source_metadata_path(repo: Path) -> Path:
    return repo / ".godot-coder-source.json"


def _source_metadata(repo: Path) -> dict[str, Any] | None:
    path = _source_metadata_path(repo)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _resolved_commit(repo: Path) -> str | None:
    commit = _git_commit(repo) if (repo / ".git").is_dir() else None
    if commit:
        return commit
    metadata = _source_metadata(repo)
    value = str((metadata or {}).get("commit") or "").strip()
    return value or None


def _github_archive_url(url: str, commit: str) -> str | None:
    parsed = urlparse(url)
    if parsed.netloc.lower() != "github.com":
        return None
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        return None
    owner, repository = parts
    if repository.endswith(".git"):
        repository = repository[:-4]
    return f"https://codeload.github.com/{owner}/{repository}/zip/{commit}"


def _safe_extract_github_archive(archive_path: Path, destination: Path, source: dict[str, Any] | None = None) -> None:
    temporary = destination.with_name(destination.name + ".archive-building")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        roots = {PurePosixPath(info.filename.replace("\\", "/")).parts[0] for info in infos if info.filename}
        if len(roots) != 1:
            raise ValueError("GitHub archive has an unexpected root layout")
        root_name = next(iter(roots))
        for info in infos:
            if info.is_dir():
                continue
            pure = PurePosixPath(info.filename.replace("\\", "/"))
            if pure.is_absolute() or ".." in pure.parts or not pure.parts or pure.parts[0] != root_name:
                raise ValueError(f"unsafe archive path: {info.filename}")
            relative = Path(*pure.parts[1:])
            if source and source.get("source_only"):
                lower_name = relative.name.lower()
                allowed_suffixes = {".gd", ".tscn", ".tres", ".cfg", ".json", ".gdextension"}
                is_license = lower_name.startswith("license") or lower_name.startswith("copying")
                if relative.suffix.lower() not in allowed_suffixes and lower_name != "project.godot" and not is_license:
                    continue
                if not is_license and (_is_excluded(relative, source) or not _source_path_allowed(relative, source)):
                    continue
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as archive_source, target.open("wb") as output:
                shutil.copyfileobj(archive_source, output, length=1024 * 1024)
    _replace_directory(temporary, destination)


def _archive_checkout(target: Path, source: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    remote = _run(["git", "ls-remote", source["url"], source["branch"]], timeout=180)
    if remote.returncode != 0 or not remote.stdout.strip():
        return remote
    commit = remote.stdout.split()[0].strip()
    archive_url = _github_archive_url(source["url"], commit)
    if not archive_url:
        return subprocess.CompletedProcess([], 1, "", "Archive fallback only supports GitHub HTTPS URLs")
    archive_path = target.with_suffix(".download.zip")
    try:
        # User-Agent comes from __version__ so it can't drift from the release.
        request = urllib.request.Request(archive_url, headers={"User-Agent": f"Godot-Coder-AI/{__version__}"})
        with urllib.request.urlopen(request, timeout=300) as response, archive_path.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        _safe_extract_github_archive(archive_path, target, source)
        expected = {"url": source["url"], "ref": source["branch"]}
        _json_write(_source_metadata_path(target), {
            **expected, "commit": commit, "updated_at": time.time(), "transport": "github-archive",
            "license_verification": verify_declared_license(target, source),
        })
        return subprocess.CompletedProcess([], 0, f"downloaded archive at {commit}", "")
    except Exception as exc:
        return subprocess.CompletedProcess([], 1, "", f"archive fallback failed: {type(exc).__name__}: {exc}")
    finally:
        archive_path.unlink(missing_ok=True)


def verify_declared_license(repo: Path, source: dict[str, Any]) -> dict[str, Any]:
    """Verify that a checked-out repository contains text matching its declared license.

    The registry declaration alone is not trusted. Only source code is staged, but a
    local license file must still match the expected SPDX family before scanning.
    """
    declared = str(source.get("license", "UNKNOWN"))
    if declared == "LicenseRef-User-Owned-Private" and str(source.get("url", "")).startswith("local://"):
        metadata = _source_metadata(repo) or {}
        confirmed = bool(source.get("owner_confirmed")) and bool(metadata.get("owner_confirmed"))
        return {
            "verified": confirmed,
            "declared": declared,
            "file": None,
            "license_file": None,
            "reason_code": None if confirmed else "ownership-not-confirmed",
            "reason": None if confirmed else "Local ownership confirmation is missing.",
            "checked_at": time.time(),
        }
    candidates: list[Path] = []
    for name in LICENSE_FILENAMES:
        candidate = repo / name
        if candidate.is_file():
            candidates.append(candidate)
    if not candidates:
        for candidate in sorted(repo.glob("LICENSE*")):
            if candidate.is_file() and candidate.stat().st_size <= 512 * 1024:
                candidates.append(candidate)
    markers = LICENSE_MARKERS.get(declared, ())
    for candidate in candidates:
        try:
            raw = candidate.read_text(encoding="utf-8", errors="replace")[:512 * 1024]
        except OSError:
            continue
        lowered = raw.lower()
        if markers and all(marker.lower() in lowered for marker in markers):
            return {
                "verified": True,
                "declared": declared,
                "file": candidate.relative_to(repo).as_posix(),
                "license_file": candidate.relative_to(repo).as_posix(),
                "reason_code": None,
                "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                "checked_at": time.time(),
            }
    return {
        "verified": False,
        "declared": declared,
        "file": candidates[0].relative_to(repo).as_posix() if candidates else None,
        "license_file": candidates[0].relative_to(repo).as_posix() if candidates else None,
        "reason_code": "license-mismatch" if candidates else "license-file-not-found",
        "reason": "No local license file matches the declared license.",
        "checked_at": time.time(),
    }


def _checkout_source(target: Path, source: dict[str, Any], *, update: bool) -> subprocess.CompletedProcess[str]:
    if str(source.get("url", "")).startswith("local://"):
        metadata = _source_metadata(target)
        if target.exists() and metadata and metadata.get("url") == source.get("url"):
            return subprocess.CompletedProcess([], 0, "local source ready", "")
        return subprocess.CompletedProcess([], 1, "", "local source directory is missing")

    current_commit = _resolved_commit(target)
    metadata = _source_metadata(target) if current_commit else None
    expected = {"url": source["url"], "ref": source["branch"]}
    if current_commit and not update and metadata and all(metadata.get(key) == value for key, value in expected.items()):
        return subprocess.CompletedProcess([], 0, "already downloaded", "")

    if target.exists() and (not (target / ".git").is_dir() or update):
        shutil.rmtree(target)
    if not target.exists():
        target.mkdir(parents=True)
        initialized = _run(["git", "init"], cwd=target, timeout=60)
        if initialized.returncode != 0:
            return initialized
        remote = _run(["git", "remote", "add", "origin", source["url"]], cwd=target, timeout=60)
        if remote.returncode != 0:
            return remote
    else:
        remote_url = _run(["git", "remote", "get-url", "origin"], cwd=target, timeout=30)
        if remote_url.returncode != 0:
            return remote_url
        if remote_url.stdout.strip() != source["url"]:
            changed = _run(["git", "remote", "set-url", "origin", source["url"]], cwd=target, timeout=30)
            if changed.returncode != 0:
                return changed

    # Initialise sparse-checkout once before the retry loop when source_only is set.
    if source.get("source_only"):
        sparse_init = _run(["git", "sparse-checkout", "init", "--no-cone"], cwd=target, timeout=60)
        if sparse_init.returncode != 0:
            return subprocess.CompletedProcess([], 1, "", f"sparse-checkout init: {sparse_init.stderr.strip() or sparse_init.stdout.strip()}")

    errors: list[str] = []
    for attempt in range(1, 4):
        fetch_command = [
            "git", "-c", "http.version=HTTP/1.1", "-c", "core.compression=0",
            "fetch", "--depth", "1", "--no-tags", "--force",
        ]
        if source.get("source_only"):
            fetch_command.append("--filter=blob:none")
        fetch_command.extend(["origin", source["branch"]])
        fetched = _run(fetch_command, cwd=target, timeout=1800)
        if fetched.returncode == 0:
            if source.get("source_only"):
                patterns = ["**/*.gd", "**/*.tscn", "**/*.tres", "**/*.cfg", "**/*.json", "**/*.gdextension", "**/project.godot", "/LICENSE*", "/COPYING*"]
                if not source.get("allow_addons"):
                    patterns.append("!**/addons/**")
                for excluded_path in source.get("exclude_paths", []):
                    normalized = str(excluded_path).replace("\\", "/").strip("/")
                    if normalized:
                        patterns.append(f"!**/{normalized}/**")
                sparse = _run(["git", "sparse-checkout", "set", "--no-cone", *patterns], cwd=target, timeout=120)
                if sparse.returncode != 0:
                    errors.append(f"attempt {attempt} sparse checkout: {sparse.stderr.strip() or sparse.stdout.strip()}")
                    continue
            checked_out = _run(["git", "checkout", "--detach", "--force", "FETCH_HEAD"], cwd=target, timeout=600)
            if checked_out.returncode == 0:
                commit = _git_commit(target)
                _json_write(_source_metadata_path(target), {
                    **expected, "commit": commit, "updated_at": time.time(), "transport": "git",
                    "license_verification": verify_declared_license(target, source),
                })
                return subprocess.CompletedProcess([], 0, fetched.stdout + checked_out.stdout, fetched.stderr + checked_out.stderr)
            errors.append(checked_out.stderr or checked_out.stdout)
        else:
            errors.append(fetched.stderr or fetched.stdout)
        for lock in (target / ".git" / "index.lock", target / ".git" / "shallow.lock"):
            lock.unlink(missing_ok=True)
        time.sleep(min(4, attempt))

    archive_result = _archive_checkout(target, source)
    if archive_result.returncode == 0:
        return archive_result
    errors.append(archive_result.stderr or archive_result.stdout)
    return subprocess.CompletedProcess([], 1, "", "\n\n".join(item.strip() for item in errors if item.strip()))


def fetch_sources(project_root: Path, *, source_ids: set[str] | None = None, update: bool = False) -> dict[str, Any]:
    registry = load_registry(project_root)
    downloads = corpus_root(project_root) / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    enabled = [source for source in registry["sources"] if source.get("enabled")]
    if source_ids is not None:
        enabled = [source for source in enabled if source["id"] in source_ids]
    if not enabled:
        raise ValueError("No activated source selected.")
    if any(not str(source.get("url", "")).startswith("local://") for source in enabled) and not _git_available():
        raise RuntimeError("Git was not found. Install Git for Windows or use local imports only.")

    for index, source in enumerate(enabled, start=1):
        source_id = source["id"]
        target = downloads / source_id
        print(f"source={index}/{len(enabled)} id={source_id} phase=download ref={source['branch']}")
        result = _checkout_source(target, source, update=update)
        if result.returncode != 0:
            print(result.stdout)
            print(result.stderr, file=sys.stderr)
            results.append({
                "id": source_id, "status": "failed", "ref": source["branch"],
                "error": (result.stderr or result.stdout).strip()
            })
            continue
        commit = _resolved_commit(target)
        metadata = _source_metadata(target) or {}
        license_verification = metadata.get("license_verification")
        if not license_verification or not license_verification.get("verified"):
            license_verification = verify_declared_license(target, source)
            metadata.update({
                "url": source["url"], "ref": source["branch"], "commit": commit,
                "updated_at": metadata.get("updated_at") or time.time(),
                "license_verification": license_verification,
            })
            _json_write(_source_metadata_path(target), metadata)
        if not license_verification.get("verified"):
            results.append({
                "id": source_id, "status": "license_failed", "ref": source["branch"],
                "error": license_verification.get("reason", "License verification failed"),
                "license_verification": license_verification,
            })
            print(f"source={index}/{len(enabled)} id={source_id} phase=license_failed", file=sys.stderr)
            continue
        size_bytes = _directory_size_bytes(target)
        results.append({
            "id": source_id,
            "status": "ready",
            "path": str(target),
            "commit": commit,
            "branch": source["branch"],
            "license": source["license"],
            "size_bytes": size_bytes,
            "license_verification": license_verification,
        })
        print(
            f"source={index}/{len(enabled)} id={source_id} phase=ready "
            f"commit={commit or 'unknown'} size_mb={size_bytes / 1024**2:.1f}"
        )

    report = {"created_at": time.time(), "sources": results}
    _json_write(corpus_root(project_root) / "fetch_report.json", report)
    failed = [item for item in results if item["status"] != "ready"]
    report["status"] = "completed_with_warnings" if failed else "completed"
    report["ready"] = len(results) - len(failed)
    report["failed"] = len(failed)
    _json_write(corpus_root(project_root) / "fetch_report.json", report)
    if failed:
        print(f"WARNING: {len(failed)} source(s) are not ready yet. Successful sources remain usable.", file=sys.stderr)
    return report


def _nearest_project(path: Path, source_root: Path) -> Path | None:
    current = path.parent
    while current != source_root.parent:
        if (current / "project.godot").exists():
            return current
        if current == source_root:
            break
        current = current.parent
    return None


def _extract_rst_gdscript(path: Path) -> Iterable[tuple[int, str]]:
    """Extract GDScript from standalone and tabbed Sphinx/RST code directives.

    Godot documentation commonly nests ``code-tab`` directives under ``tabs``.
    A sibling code tab is indented too, so extraction must stop at the directive's
    indentation level instead of consuming the following C# block.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    index = 0
    snippet = 0
    directive = re.compile(
        r"^(?P<indent>[ \t]*)\.\.\s+(?:code-block|code|code-tab)::\s+"
        r"(?:gdscript|GDScript)(?:\s+.*)?$"
    )
    while index < len(lines):
        match = directive.match(lines[index])
        if not match:
            index += 1
            continue
        directive_indent = len(match.group("indent").expandtabs(4))
        index += 1
        while index < len(lines):
            stripped = lines[index].strip()
            indentation = len(lines[index]) - len(lines[index].lstrip(" \t"))
            if not stripped or (indentation > directive_indent and lines[index].lstrip().startswith(":")):
                index += 1
                continue
            break
        block: list[str] = []
        while index < len(lines):
            line = lines[index]
            if not line.strip():
                block.append(line)
                index += 1
                continue
            indentation = len(line.expandtabs(4)) - len(line.lstrip(" \t").expandtabs(4))
            if indentation <= directive_indent:
                break
            block.append(line)
            index += 1
        code = textwrap.dedent("\n".join(block)).strip("\n")
        if len(code) >= 24 and "..." not in code and not code.lstrip().startswith(("# Output:", "# Prints")):
            snippet += 1
            yield snippet, code + "\n"


def _group_for_demo(path: Path, source_root: Path) -> tuple[str, Path | None]:
    project = _nearest_project(path, source_root)
    if project:
        return project.relative_to(source_root).as_posix(), project
    return path.parent.relative_to(source_root).as_posix() or "root", None


def _stage_source(
    source: dict[str, Any],
    source_index: int,
    total: int,
    downloads: Path,
    staging_work: Path,
    hash_lock: threading.Lock,
    seen_hashes: dict[str, str],
) -> tuple[list[CorpusRecord], list[dict[str, str]], dict[str, Any] | None, dict[str, Any] | None]:
    """Process one corpus source and return (records, skipped, source_manifest, unavailable_info)."""
    source_root = downloads / source["id"]
    local_skipped: list[dict[str, str]] = []

    if not source_root.exists():
        info = {"id": source["id"], "title": source["title"], "reason": "not-downloaded"}
        local_skipped.append({"source": source["id"], "path": "", "reason": "source-unavailable:not-downloaded"})
        print(f"source={source_index}/{total} id={source['id']} phase=skip reason=not-downloaded", file=sys.stderr)
        return [], local_skipped, None, info

    license_verification = verify_declared_license(source_root, source)
    if not license_verification.get("verified"):
        info = {"id": source["id"], "title": source["title"], "reason": "license-not-verified",
                "detail": license_verification.get("reason")}
        local_skipped.append({"source": source["id"], "path": "", "reason": "source-unavailable:license-not-verified"})
        print(f"source={source_index}/{total} id={source['id']} phase=skip reason=license-not-verified", file=sys.stderr)
        return [], local_skipped, None, info

    commit = _resolved_commit(source_root)
    source_count = 0
    print(f"source={source_index}/{total} id={source['id']} phase=scan")

    candidates: Iterable[tuple[str, str, str, Path | None]]
    if source["kind"] == "godot_projects":
        items: list[tuple[str, str, str, Path | None]] = []
        for path in sorted(source_root.rglob("*.gd")):
            relative = path.relative_to(source_root)
            if _is_excluded(relative, source) or not _source_path_allowed(relative, source) or path.stat().st_size > 256 * 1024:
                local_skipped.append({"source": source["id"], "path": relative.as_posix(), "reason": "filtered"})
                continue
            group, project = _group_for_demo(path, source_root)
            items.append((relative.as_posix(), path.read_text(encoding="utf-8", errors="replace"), group, project))
        candidates = items
    else:
        items = []
        for path in sorted(source_root.rglob("*.rst")):
            relative = path.relative_to(source_root)
            if _is_excluded(relative, source) or not _source_path_allowed(relative, source):
                continue
            for number, code in _extract_rst_gdscript(path):
                items.append((f"{relative.as_posix()}#gdscript-{number:03d}", code, relative.as_posix(), None))
        candidates = items

    local_records: list[CorpusRecord] = []
    for original_path, raw_text, group, project in candidates:
        text = _normalize_code(raw_text)
        if len(text.strip()) < 20:
            local_skipped.append({"source": source["id"], "path": original_path, "reason": "too-short"})
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with hash_lock:
            if digest in seen_hashes:
                local_skipped.append({"source": source["id"], "path": original_path, "reason": f"duplicate:{seen_hashes[digest]}"})
                continue
            seen_hashes[digest] = f"{source['id']}:{original_path}"
        group_id = f"{source['id']}::{group}"
        split_policy = str(source.get("split_policy") or "grouped")
        split = split_policy if split_policy in {"train", "val", "test"} else _split_for_group(group_id)
        record_id = hashlib.sha256(f"{source['id']}\0{original_path}\0{digest}".encode()).hexdigest()[:20]
        staged_relative = Path(source["id"]) / f"{record_id}.gd"
        staged_path = staging_work / staged_relative
        staged_path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            f"# corpus_source: {source['id']}\n"
            f"# original_path: {original_path}\n"
            f"# license: {source['license']}\n"
            f"# private_source: {str(not bool(source.get('redistribution_allowed', True))).lower()}\n"
        )
        staged_path.write_text(header + text, encoding="utf-8", newline="\n")
        validation_status = "not_required" if source["kind"] == "rst_gdscript" else "pending"
        local_records.append(CorpusRecord(
            record_id=record_id,
            source_id=source["id"],
            source_title=source["title"],
            group_id=group_id,
            kind=source["kind"],
            original_path=original_path,
            staged_path=staged_relative.as_posix(),
            split=split,
            content_sha256=digest,
            bytes=len(text.encode("utf-8")),
            license=source["license"],
            attribution=source["attribution"],
            source_url=source.get("url"),
            source_commit=commit,
            source_ref=source.get("branch"),
            project_root=str(project) if project else None,
            validation_status=validation_status,
        ))
        source_count += 1
    source_manifest = {
        "id": source["id"], "title": source["title"], "url": source["url"], "branch": source["branch"],
        "commit": commit, "license": source["license"], "attribution": source["attribution"], "accepted": source_count,
        "license_scope": source.get("license_scope"),
        "owner_confirmed": bool(source.get("owner_confirmed", False)),
        "redistribution_allowed": bool(source.get("redistribution_allowed", True)),
    }
    print(f"source={source_index}/{total} id={source['id']} accepted={source_count}")
    return local_records, local_skipped, source_manifest, None


def build_staging(project_root: Path) -> dict[str, Any]:
    registry = load_registry(project_root)
    downloads = corpus_root(project_root) / "downloads"
    staging = corpus_root(project_root) / "staged"
    staging_work = corpus_root(project_root) / "staged.building"
    if staging_work.exists():
        shutil.rmtree(staging_work)
    staging_work.mkdir(parents=True)
    records: list[CorpusRecord] = []
    skipped: list[dict[str, str]] = []
    seen_hashes: dict[str, str] = {}
    source_manifests: list[dict[str, Any]] = []

    enabled = [source for source in registry["sources"] if source.get("enabled")]
    if not enabled:
        raise ValueError("No source is activated.")

    hash_lock = threading.Lock()
    max_workers = min(8, (os.cpu_count() or 4))
    total = len(enabled)
    results_by_index: dict[int, tuple[list[CorpusRecord], list[dict[str, str]], dict[str, Any] | None, dict[str, Any] | None]] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(_stage_source, src, idx, total, downloads, staging_work, hash_lock, seen_hashes): idx
            for idx, src in enumerate(enabled, start=1)
        }
        for future in concurrent.futures.as_completed(future_to_index):
            results_by_index[future_to_index[future]] = future.result()

    unavailable_sources: list[dict[str, Any]] = []
    for idx in range(1, total + 1):
        local_records, local_skipped, source_manifest, unavailable = results_by_index[idx]
        records.extend(local_records)
        skipped.extend(local_skipped)
        if source_manifest is not None:
            source_manifests.append(source_manifest)
        if unavailable is not None:
            unavailable_sources.append(unavailable)

    # Ensure all splits exist without moving records from the same group across splits.
    groups_by_split: dict[str, set[str]] = {name: set() for name in ("train", "val", "test")}
    for record in records:
        groups_by_split[record.split].add(record.group_id)
    if not groups_by_split["val"] or not groups_by_split["test"]:
        explicit_groups = {
            f"{source['id']}::" for source in enabled
            if str(source.get("split_policy") or "grouped") in {"train", "val", "test"}
        }
        groups = sorted(
            {record.group_id for record in records if not any(record.group_id.startswith(prefix) for prefix in explicit_groups)},
            key=lambda item: hashlib.sha256(item.encode()).hexdigest(),
        )
        if len(groups) >= 3:
            forced = {groups[0]: "val", groups[1]: "test"}
            for record in records:
                if record.group_id in forced:
                    record.split = forced[record.group_id]

    if not records:
        shutil.rmtree(staging_work, ignore_errors=True)
        raise RuntimeError("No usable source could be scanned. Check the download and license status.")
    _replace_directory(staging_work, staging)
    manifest = {
        "format": "godot-coder-licensed-corpus",
        "format_version": CORPUS_FORMAT_VERSION,
        "created_at": time.time(),
        "records": [asdict(record) for record in records],
        "sources": source_manifests,
        "unavailable_sources": unavailable_sources,
        "skipped": skipped,
        "summary": _summary(records, skipped),
    }
    _json_write(corpus_root(project_root) / "corpus_manifest.json", manifest)
    _json_write(corpus_root(project_root) / "LICENSE_MANIFEST.json", {
        "generated_at": time.time(),
        "notice": "Public sources retain their licenses and attribution. Sources with redistribution_allowed=false are private and must never be redistributed in source or derived corpus bundles.",
        "contains_private_sources": any(not item.get("redistribution_allowed", True) for item in source_manifests),
        "sources": source_manifests,
    })
    print(json.dumps(manifest["summary"], indent=2))
    return manifest


def _summary(records: Iterable[CorpusRecord | dict[str, Any]], skipped: list[dict[str, str]] | None = None) -> dict[str, Any]:
    normalized = [record if isinstance(record, dict) else asdict(record) for record in records]
    return {
        "records": len(normalized),
        "train": sum(item["split"] == "train" for item in normalized),
        "val": sum(item["split"] == "val" for item in normalized),
        "test": sum(item["split"] == "test" for item in normalized),
        "bytes": sum(int(item["bytes"]) for item in normalized),
        "pending_validation": sum(item["validation_status"] == "pending" for item in normalized),
        "passed_validation": sum(item["validation_status"] == "passed" for item in normalized),
        "failed_validation": sum(item["validation_status"] == "failed" for item in normalized),
        "skipped": len(skipped or []),
    }


def _find_godot() -> str | None:
    for name in ("godot", "godot4", "godot.CMD", "godot.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _validation_cache_path(project_root: Path) -> Path:
    # v2 is intentionally separate: v1 cached false failures from invoking
    # ordinary Node/Resource scripts through ``--script --check-only``.
    return corpus_root(project_root) / "cache" / "godot_project_validation_v2.json"


def _load_validation_cache(project_root: Path) -> dict[str, Any]:
    path = _validation_cache_path(project_root)
    if not path.exists():
        return {"format_version": 2, "validator": "project-aware-v2", "entries": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("format_version") == 2 and payload.get("validator") == "project-aware-v2":
            return payload
    except (OSError, ValueError, TypeError):
        pass
    return {"format_version": 2, "validator": "project-aware-v2", "entries": {}}


def _validation_timeout_seconds() -> float:
    """Max seconds for one Godot --import / --check-only run in corpus validation.

    Same variable and default as local_sources.py (GODOT_CODER_VALIDATION_TIMEOUT_SECONDS,
    default 120). corpus.py must keep its own copy - local_sources imports corpus,
    so importing back would create a cycle.
    """
    raw = os.environ.get("GODOT_CODER_VALIDATION_TIMEOUT_SECONDS", "120")
    try:
        return min(1800.0, max(1.0, float(raw)))
    except (TypeError, ValueError):
        return 120.0


def _validation_idle_timeout_seconds() -> float:
    """Idle timeout (no output) for Godot runs. Same variable as local_sources.py."""
    raw = os.environ.get("GODOT_CODER_VALIDATION_IDLE_TIMEOUT_SECONDS", "30")
    try:
        return min(600.0, max(5.0, float(raw)))
    except (TypeError, ValueError):
        return 30.0


def _godot_version(executable: str) -> str:
    result = run_managed_process([executable, "--version"], timeout_seconds=20, idle_timeout_seconds=10.0)
    if result.return_code != 0:
        return "unknown"
    text = (result.output or "").strip()
    return text.splitlines()[0] if text else "unknown"


_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_GDSCRIPT_PATH_RE = re.compile(
    r"(?P<path>(?:res://)?[^\s\"'()]+?\.gd)(?::(?P<line>\d+))?(?=$|[\s\"'):,])",
    re.IGNORECASE,
)
_CONTEXT_ERROR_MARKERS = (
    "could not find type", "could not resolve", "could not preload", "can't preload",
    "failed to load resource", "cannot load resource", "couldn't load resource",
    "could not find base class", "base class could not be resolved", "not declared in the current scope",
    "identifier not found", "nonexistent function", "invalid call", "autoload",
    "failed to instantiate", "failed to load script", "cyclic reference",
)
_HARD_SYNTAX_MARKERS = (
    "expected ", "unexpected ", "invalid indentation", "mixed use of tabs and spaces",
    "unterminated", "unclosed", "invalid numeric constant", "invalid escape sequence",
    "expected end of statement", "expected closing", "expected expression", "expected statement",
    "parser bug", "tokenizer error",
)


def _clean_godot_output(output: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", output).replace("\r\n", "\n")


def _declared_godot_generation(project: Path) -> str:
    """Return godot4, godot3, or unknown from project.godot without modifying it."""
    try:
        content = (project / "project.godot").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unknown"
    feature_match = re.search(r"config/features\s*=\s*PackedStringArray\((.*?)\)", content, re.DOTALL)
    features = feature_match.group(1) if feature_match else ""
    if re.search(r'[\"\']4(?:\.|[\"\'])', features):
        return "godot4"
    version_match = re.search(r"(?m)^\s*config_version\s*=\s*(\d+)", content)
    if version_match:
        version = int(version_match.group(1))
        if version >= 5:
            return "godot4"
        if version <= 4:
            return "godot3"
    return "unknown"


def _record_project_path(project_root: Path, record: dict[str, Any]) -> str | None:
    project_value = record.get("project_root")
    if not project_value:
        return None
    project = Path(project_value)
    source_script = corpus_root(project_root) / "downloads" / record["source_id"] / record["original_path"]
    try:
        return source_script.resolve().relative_to(project.resolve()).as_posix().lower()
    except (OSError, ValueError):
        return None


def _paths_near_error(lines: list[str], index: int) -> set[str]:
    block = "\n".join(lines[max(0, index - 2): min(len(lines), index + 4)])
    paths: set[str] = set()
    for match in _GDSCRIPT_PATH_RE.finditer(block):
        path = match.group("path").replace("\\", "/")
        if path.lower().startswith("res://"):
            path = path[6:]
        paths.add(path.lstrip("./").lower())
    return paths


def _classify_project_output(output: str) -> dict[str, Any]:
    """Extract only high-confidence syntax failures from a project import.

    Dependency, asset, autoload and plugin errors are retained as context
    warnings. They must not erase otherwise useful source-only GDScript.
    """
    cleaned = _clean_godot_output(output)
    lines = cleaned.splitlines()
    hard_failures: dict[str, str] = {}
    warning_lines: list[str] = []
    unmatched_hard_errors: list[str] = []
    for index, line in enumerate(lines):
        lower = line.lower().strip()
        if not lower:
            continue
        is_error = "parse error" in lower or "script error" in lower or lower.startswith("error:")
        if not is_error:
            continue
        block = " ".join(lines[max(0, index - 1): min(len(lines), index + 3)]).strip()
        block_lower = block.lower()
        is_context = any(marker in block_lower for marker in _CONTEXT_ERROR_MARKERS)
        is_hard = any(marker in block_lower for marker in _HARD_SYNTAX_MARKERS)
        paths = _paths_near_error(lines, index)
        if is_hard and not is_context:
            if paths:
                for path in paths:
                    hard_failures.setdefault(path, block[:700])
            else:
                unmatched_hard_errors.append(block[:700])
        else:
            warning_lines.append(block[:700])
    return {
        "hard_failures": hard_failures,
        "context_warnings": list(dict.fromkeys(warning_lines))[:100],
        "unmatched_hard_errors": list(dict.fromkeys(unmatched_hard_errors))[:50],
        "clean_output_tail": cleaned[-4000:],
    }


def _project_cache_key(
    project_root: Path,
    project: Path,
    records: list[dict[str, Any]],
    godot_version: str,
) -> str:
    try:
        project_settings_hash = hashlib.sha256((project / "project.godot").read_bytes()).hexdigest()
    except OSError:
        project_settings_hash = "missing"
    record_fingerprints = sorted(
        f"{item.get('original_path')}:{item.get('content_sha256')}:{item.get('source_commit')}"
        for item in records
    )
    payload = "\0".join([
        "project-aware-v2", str(project.resolve()), project_settings_hash, godot_version,
        *record_fingerprints,
    ])
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def _write_project_checker(project_root: Path, cache_key: str, relative_paths: list[str]) -> Path:
    helper_dir = project_root / "reports" / "corpus_validation_helpers"
    helper_dir.mkdir(parents=True, exist_ok=True)
    helper = helper_dir / f"project_check_{cache_key[:16]}.gd"
    resource_paths = [f"res://{path}" for path in relative_paths]
    payload = json.dumps(resource_paths, ensure_ascii=False)
    helper.write_text(
        "extends SceneTree\n\n"
        "func _initialize() -> void:\n"
        f"\tvar paths: Array = {payload}\n"
        "\tvar failed := 0\n"
        "\tfor path_value in paths:\n"
        "\t\tvar path := str(path_value)\n"
        "\t\tprint(\"GCAI_CHECK_BEGIN\\t\" + path)\n"
        "\t\tvar resource := ResourceLoader.load(path, \"\", ResourceLoader.CACHE_MODE_IGNORE)\n"
        "\t\tif resource == null:\n"
        "\t\t\tfailed += 1\n"
        "\t\t\tprint(\"GCAI_CHECK_NULL\\t\" + path)\n"
        "\t\telse:\n"
        "\t\t\tprint(\"GCAI_CHECK_OK\\t\" + path)\n"
        "\tprint(\"GCAI_CHECK_SUMMARY\\ttotal=\" + str(paths.size()) + \"\\tfailed=\" + str(failed))\n"
        "\tquit(failed)\n",
        encoding="utf-8",
        newline="\n",
    )
    return helper


def _checker_results(output: str) -> tuple[set[str], set[str]]:
    passed: set[str] = set()
    null: set[str] = set()
    for line in _clean_godot_output(output).splitlines():
        if line.startswith("GCAI_CHECK_OK\t"):
            path = line.split("\t", 1)[1].strip().replace("\\", "/")
            passed.add(path.removeprefix("res://").lower())
        elif line.startswith("GCAI_CHECK_NULL\t"):
            path = line.split("\t", 1)[1].strip().replace("\\", "/")
            null.add(path.removeprefix("res://").lower())
    return passed, null


def _validate_project_group(
    project_root: Path,
    project: Path,
    records: list[dict[str, Any]],
    godot: str,
    godot_version: str,
    cache_entries: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], bool]:
    """Validate one project once and classify each record conservatively."""
    key = _project_cache_key(project_root, project, records, godot_version)
    cached = cache_entries.get(key)
    if isinstance(cached, dict) and cached.get("validator") == "project-aware-v2":
        result_map = cached.get("records") or {}
        if all(item.get("record_id") in result_map for item in records):
            return result_map, cached.get("project_result") or {}, True

    generation = _declared_godot_generation(project)
    result_map: dict[str, dict[str, Any]] = {}
    project_result: dict[str, Any] = {
        "project": str(project), "generation": generation, "scripts": len(records),
        "status": "pending", "return_code": None, "warning_count": 0, "hard_failures": 0,
    }
    if generation == "godot3":
        message = "Godot 3 project detected (project.godot config_version <= 4); not taken into the Godot 4 corpus."
        for item in records:
            result_map[item["record_id"]] = {
                "status": "failed", "classification": "legacy_godot3", "error": message,
            }
        project_result.update(status="legacy_godot3", hard_failures=len(records))
    elif not (project / "project.godot").exists():
        # The project directory vanished between staging and validation. Not a
        # syntax error and not a Godot-3 file - keep the records as warnings
        # instead of discarding them (policy: only those two are hard excludes).
        message = "No matching project.godot found; keep the file as a context warning without project validation."
        for item in records:
            result_map[item["record_id"]] = {
                "status": "passed", "classification": "context_warning", "error": message,
            }
        project_result.update(status="missing_project", warning_count=len(records))
    else:
        relative_by_record = {
            item["record_id"]: _record_project_path(project_root, item)
            for item in records
        }
        relative_paths = sorted({path for path in relative_by_record.values() if path})
        import_timed_out = False
        # Managed runner: a Mono Godot build spawns a grandchild that inherits the
        # stdout/stderr pipe handles. Raw subprocess.run with capture_output can
        # then deadlock forever on timeout (only the direct child is killed, so
        # communicate() blocks reading a pipe the grandchild still holds open).
        # The job object inside run_managed_process terminates the whole tree.
        import_result = run_managed_process(
            build_project_validation_command(godot, project),
            timeout_seconds=_validation_timeout_seconds(),
            idle_timeout_seconds=_validation_idle_timeout_seconds(),
        )
        import_output = import_result.output.strip()
        import_return_code = import_result.return_code
        if import_result.timed_out:
            import_timed_out = True
            import_return_code = None
        if import_result.startup_error:
            import_output = import_output or f"Godot could not be started: {import_result.startup_error}"
            import_return_code = None

        checker = _write_project_checker(project_root, key, relative_paths)
        checker_timed_out = False
        checker_result = run_managed_process(
            build_project_script_command(godot, project, checker),
            timeout_seconds=max(120.0, _validation_timeout_seconds()),
            idle_timeout_seconds=_validation_idle_timeout_seconds(),
        )
        checker_output = checker_result.output.strip()
        checker_return_code = checker_result.return_code
        if checker_result.timed_out:
            checker_timed_out = True
            checker_return_code = None
        if checker_result.startup_error:
            checker_output = checker_output or f"Godot could not be started: {checker_result.startup_error}"
            checker_return_code = None

        output = "\n--- project import ---\n" + import_output + "\n--- project script checker ---\n" + checker_output
        analysis = _classify_project_output(output)
        hard_by_path = analysis["hard_failures"]
        warnings = analysis["context_warnings"]
        unmatched = analysis["unmatched_hard_errors"]
        checker_passed, checker_null = _checker_results(checker_output)
        marker_count = len(checker_passed | checker_null)
        warning_summary = None
        if checker_timed_out:
            warning_summary = "The project script check exceeded its time limit; unconfirmed scripts stay as context warnings."
        elif import_timed_out:
            warning_summary = "The Godot project import exceeded its time limit; the per-file check still ran."
        elif warnings:
            warning_summary = warnings[0]
        elif unmatched:
            warning_summary = "Godot reported a parser error that could not be clearly attributed to a file; no file was rejected outright."
        elif checker_return_code not in (0, None):
            warning_summary = f"The project checker exited with code {checker_return_code}; only clearly attributed syntax errors are rejected."
        elif import_return_code not in (0, None):
            warning_summary = f"The Godot project import exited with code {import_return_code} but without a clear file-related syntax error."

        hard_count = 0
        for item in records:
            rel = relative_by_record[item["record_id"]]
            matched_error = None
            if rel and rel not in checker_passed:
                matched_error = hard_by_path.get(rel)
                if matched_error is None:
                    matches = [error for path, error in hard_by_path.items() if path.endswith("/" + rel) or rel.endswith("/" + path)]
                    if len(matches) == 1:
                        matched_error = matches[0]
            if matched_error:
                hard_count += 1
                result_map[item["record_id"]] = {
                    "status": "failed", "classification": "syntax_error", "error": matched_error,
                }
                continue

            classification = "project_passed"
            error = None
            if rel is None:
                # The script lives outside the resolved project directory (for
                # example a stray file next to the repo root). We cannot
                # attribute any checker result to it, but per policy a missing
                # mapping is no syntax error - keep it with a warning.
                classification = "context_warning"
                error = "The script lies outside the project; no clear attribution is possible."
            elif rel in checker_null:
                classification = "context_warning"
                error = warning_summary or "The script could not be loaded in the incomplete source project; no clear syntax error detected."
            elif marker_count and rel not in checker_passed:
                classification = "context_warning"
                error = warning_summary or "The project checker returned no result for this file; it is not rejected outright."
            elif warning_summary is not None and not checker_passed:
                classification = "context_warning"
                error = warning_summary
            result_map[item["record_id"]] = {
                "status": "passed", "classification": classification, "error": error,
            }

        project_status = "passed"
        if hard_count:
            project_status = "passed_with_file_failures"
        elif any(value.get("classification") == "context_warning" for value in result_map.values()):
            project_status = "passed_with_context_warnings"
        project_result.update(
            status=project_status,
            return_code=checker_return_code,
            import_return_code=import_return_code,
            checker_return_code=checker_return_code,
            import_timed_out=import_timed_out,
            checker_timed_out=checker_timed_out,
            checker_markers=marker_count,
            warning_count=len(warnings) + len(unmatched) + int(import_timed_out) + int(checker_timed_out),
            hard_failures=hard_count,
            unmatched_hard_errors=unmatched,
            output_tail=analysis["clean_output_tail"],
        )

    cache_entries[key] = {
        "validator": "project-aware-v2", "checked_at": time.time(), "godot": godot_version,
        "records": result_map, "project_result": project_result,
    }
    return result_map, project_result, False


def _validate_standalone_records(
    project_root: Path,
    records: list[dict[str, Any]],
    godot: str,
    godot_version: str,
    cache_entries: dict[str, Any],
) -> int:
    """Validate project-less records (addon-only repos) per file with --check-only.

    Returns the number of hard failures. Records with a clear syntax error are
    failed; everything else is kept as a context warning. Results are cached in
    the same validation cache under a ``standalone:`` key prefix.
    """
    per_file_timeout = _validation_timeout_seconds()
    hard_count = 0
    for item in records:
        source_root = corpus_root(project_root) / "downloads" / item["source_id"]
        script = source_root / item["original_path"]
        cache_key = hashlib.sha256(
            f"standalone:{item['source_id']}:{item['original_path']}:{item['content_sha256']}:{godot_version}".encode("utf-8")
        ).hexdigest()
        cached = cache_entries.get(cache_key)
        if isinstance(cached, dict):
            status = cached.get("status", "passed")
            classification = cached.get("classification", "context_warning")
            error = cached.get("error")
        elif not script.exists():
            # Intentional: the downloads copy is gone, but the staged copy (which
            # is what actually lands in prepared/) still exists. Per policy a
            # missing source file is no syntax error - keep the record with a
            # warning instead of silently dropping it.
            status, classification = "passed", "context_warning"
            error = "Source file missing while checking; keep as a context warning."
        else:
            result = run_managed_process(
                build_check_command(godot, source_root, script),
                timeout_seconds=per_file_timeout,
                idle_timeout_seconds=min(per_file_timeout, _validation_idle_timeout_seconds()),
            )
            output = result.output.strip()
            if result.startup_error:
                output = output or f"Godot could not be started: {result.startup_error}"
            relative = script.resolve().relative_to(source_root.resolve()).as_posix().lower()
            analysis = _classify_project_output(output)
            matched = analysis["hard_failures"].get(relative)
            if matched is None:
                matches = [
                    error for path, error in analysis["hard_failures"].items()
                    if path.endswith("/" + relative) or relative.endswith("/" + path)
                ]
                if len(matches) == 1:
                    matched = matches[0]
            if matched:
                status, classification, error = "failed", "syntax_error", matched
            else:
                status, classification = "passed", "context_warning"
                if result.timed_out:
                    error = "The per-file check exceeded its time limit; no clear syntax error - keep as a context warning."
                elif warnings_ := analysis["context_warnings"]:
                    error = warnings_[0]
                else:
                    error = "Checked as an add-on script without project.godot; no clear syntax error detected."
        item["validation_status"] = status
        item["validation_error"] = error
        item["validation_classification"] = classification
        item["validation_engine"] = "project-aware-v2"
        hard_count += int(status == "failed")
        if not isinstance(cached, dict):
            cache_entries[cache_key] = {
                "validator": "project-aware-v2-standalone", "checked_at": time.time(),
                "godot": godot_version, "status": status, "classification": classification, "error": error,
            }
    return hard_count


def validate_and_finalize(project_root: Path, *, include_docs: bool = True, minimum_accepted: int = 10) -> dict[str, Any]:
    manifest_path = corpus_root(project_root) / "corpus_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError("The corpus has not been scanned yet.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = manifest["records"]

    # v0.7.6 and earlier may have persisted false failures. Force one clean
    # project-aware pass even when the old job completed or was interrupted.
    previous_engine = manifest.get("validation_engine")
    revalidated_records = 0
    if previous_engine != "project-aware-v2":
        for record in records:
            if record.get("kind") == "godot_projects":
                record["validation_status"] = "pending"
                record["validation_error"] = None
                record.pop("validation_classification", None)
                revalidated_records += 1

    pending = [item for item in records if item.get("validation_status") == "pending"]
    godot = _find_godot()
    if pending and not godot:
        raise FileNotFoundError("Godot was not found. The code sources cannot be reliably validated yet.")
    godot_version = _godot_version(godot) if godot else "unavailable"
    validation_cache = _load_validation_cache(project_root)
    cache_entries = validation_cache.setdefault("entries", {})

    project_groups: dict[str, list[dict[str, Any]]] = {}
    missing_project_records: list[dict[str, Any]] = []
    for record in pending:
        value = record.get("project_root")
        if value:
            project_groups.setdefault(str(Path(value).resolve()), []).append(record)
        else:
            missing_project_records.append(record)

    cache_hits = 0
    project_results: list[dict[str, Any]] = []
    processed = sum(item.get("validation_status") != "pending" for item in records)
    # Records without a project.godot (pure addon repos like dialogic or
    # phantom-camera) are still real GDScript. Per policy only clear syntax
    # errors and Godot-3 files are hard excludes, so validate each file
    # standalone with Godot's --check-only parser instead of discarding it.
    standalone_hard = _validate_standalone_records(
        project_root, missing_project_records, str(godot), godot_version, cache_entries,
    ) if missing_project_records and godot else 0
    if missing_project_records:
        processed += len(missing_project_records)
        print(serialize_event({
            "event": "corpus_validation_progress",
            "level": "warning" if standalone_hard else "info",
            "phase": "corpus_validation",
            "phase_label": "Project-based corpus validation",
            "phase_status": "running",
            "project_index": 0,
            "project_total": len(project_groups),
            "project_name": "(no project)",
            "source_name": missing_project_records[0].get("source_id"),
            "file_index": processed,
            "file_total": len(records),
            "passed": sum(item.get("validation_status") == "passed" for item in records),
            "failed": standalone_hard,
            "warnings": sum(item.get("validation_classification") == "context_warning" for item in records),
            "accepted": sum(item.get("validation_status") == "passed" for item in records),
            "overall_progress": processed / max(1, len(records)),
            "message": (
                f"Add-on scripts without a project: {len(missing_project_records)} checked individually · "
                f"{standalone_hard} hard exclusions"
            ),
        }))

    total_projects = len(project_groups)
    for project_index, (project_value, group_records) in enumerate(project_groups.items(), start=1):
        project = Path(project_value)
        result_map, project_result, from_cache = _validate_project_group(
            project_root, project, group_records, str(godot), godot_version, cache_entries,
        )
        cache_hits += int(from_cache)
        project_result["source_id"] = group_records[0].get("source_id")
        project_result["project_index"] = project_index
        project_result["project_total"] = total_projects
        project_result["cache_hit"] = from_cache
        project_results.append(project_result)
        for record in group_records:
            result = result_map[record["record_id"]]
            record["validation_status"] = result["status"]
            record["validation_error"] = result.get("error")
            record["validation_classification"] = result.get("classification")
            record["validation_engine"] = "project-aware-v2"
            processed += 1
        passed_now = sum(item.get("validation_status") == "passed" for item in records)
        failed_now = sum(item.get("validation_status") == "failed" for item in records)
        warnings_now = sum(item.get("validation_classification") == "context_warning" for item in records)
        accepted_now = passed_now + (sum(item.get("validation_status") == "not_required" for item in records) if include_docs else 0)
        print(serialize_event({
            "event": "corpus_validation_progress",
            "level": "warning" if project_result.get("hard_failures") else "info",
            "phase": "corpus_validation",
            "phase_label": "Project-based corpus validation",
            "phase_status": "running",
            "project_index": project_index,
            "project_total": total_projects,
            "project_name": project.name or str(project),
            "source_name": group_records[0].get("source_id"),
            "file_index": processed,
            "file_total": len(records),
            "passed": passed_now,
            "failed": failed_now,
            "warnings": warnings_now,
            "accepted": accepted_now,
            "overall_progress": processed / max(1, len(records)),
            "message": (
                f"Checked project {project_index}/{total_projects} · "
                f"{len(group_records)} scripts · Status {project_result.get('status')}"
            ),
        }))

    staged = corpus_root(project_root) / "staged"
    prepared = corpus_root(project_root) / "prepared"
    prepared_work = corpus_root(project_root) / "prepared.building"
    if prepared_work.exists():
        shutil.rmtree(prepared_work)
    prepared_work.mkdir(parents=True)
    accepted = 0
    for record in records:
        should_include = record.get("validation_status") == "passed" or (
            include_docs and record.get("validation_status") == "not_required"
        )
        if not should_include:
            record.pop("prepared_path", None)
            continue
        source = staged / record["staged_path"]
        if not source.exists():
            record["validation_status"] = "failed"
            record["validation_classification"] = "missing_staged_file"
            record["validation_error"] = "The cleaned staging file is missing."
            record.pop("prepared_path", None)
            continue
        destination = prepared_work / record["split"] / record["source_id"] / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        record["prepared_path"] = destination.relative_to(prepared_work).as_posix()
        accepted += 1

    _replace_directory(prepared_work, prepared)
    passed = sum(item.get("validation_status") == "passed" for item in records)
    failures = sum(item.get("validation_status") == "failed" for item in records)
    context_warnings = sum(item.get("validation_classification") == "context_warning" for item in records)
    classification_counts: dict[str, int] = {}
    source_results: dict[str, dict[str, Any]] = {}
    for item in records:
        classification = str(item.get("validation_classification") or item.get("validation_status") or "unknown")
        classification_counts[classification] = classification_counts.get(classification, 0) + 1
        source_id = str(item.get("source_id") or "unknown")
        source = source_results.setdefault(source_id, {
            "source_id": source_id, "records": 0, "passed": 0, "failed": 0,
            "not_required": 0, "context_warnings": 0, "prepared": 0, "classifications": {},
        })
        source["records"] += 1
        status_value = str(item.get("validation_status") or "unknown")
        if status_value in source:
            source[status_value] += 1
        if classification == "context_warning":
            source["context_warnings"] += 1
        if item.get("prepared_path"):
            source["prepared"] += 1
        source["classifications"][classification] = source["classifications"].get(classification, 0) + 1

    manifest["validated_at"] = time.time()
    manifest["validation_engine"] = "project-aware-v2"
    manifest["validation_godot"] = godot_version
    manifest["summary"] = _summary(records, manifest.get("skipped", [])) | {
        "prepared": accepted, "context_warnings": context_warnings,
    }
    _json_write(manifest_path, manifest)
    validation_cache.update({
        "format_version": 2, "validator": "project-aware-v2", "godot_version": godot_version,
        "updated_at": time.time(),
    })
    _json_write(_validation_cache_path(project_root), validation_cache)

    report = {
        "created_at": time.time(),
        "validator": "project-aware-v2",
        "godot": godot,
        "godot_version": godot_version,
        "revalidated_legacy_records": revalidated_records,
        "cache_hits": cache_hits,
        "standalone_records": len(missing_project_records),
        "standalone_hard_excludes": standalone_hard,
        "projects": len(project_results),
        "records": len(records),
        "passed": passed,
        "failed": failures,
        "context_warnings": context_warnings,
        "not_required": sum(item.get("validation_status") == "not_required" for item in records),
        "prepared": accepted,
        "classifications": classification_counts,
        "source_results": sorted(source_results.values(), key=lambda item: (-item["records"], item["source_id"])),
        "project_results": project_results,
        "failures": [
            {
                "source_id": item["source_id"], "path": item["original_path"],
                "classification": item.get("validation_classification"), "error": item.get("validation_error"),
            }
            for item in records if item.get("validation_status") == "failed"
        ],
        "warnings": [
            {
                "source_id": item["source_id"], "path": item["original_path"],
                "classification": item.get("validation_classification"), "warning": item.get("validation_error"),
            }
            for item in records if item.get("validation_classification") == "context_warning"
        ][:500],
    }
    _json_write(corpus_root(project_root) / "validation_report.json", report)
    print(serialize_event({
        "event": "corpus_validation_completed",
        "level": "warning" if failures else "info",
        "phase": "corpus_validation",
        "phase_label": "Project-based corpus validation",
        "phase_status": "completed",
        "file_index": len(records),
        "file_total": len(records),
        "passed": passed,
        "failed": failures,
        "warnings": context_warnings,
        "accepted": accepted,
        "overall_progress": 1.0,
        "message": (
            f"Corpus validation finished · {accepted} accepted · "
            f"{failures} hard exclusions · {context_warnings} context warnings"
        ),
    }))
    if accepted < minimum_accepted:
        raise RuntimeError("Too few validated examples were prepared. Check the sources and the error report.")
    return report

def train_bpe(project_root: Path, *, vocab_size: int = 8192, min_frequency: int = 2) -> dict[str, Any]:
    prepared = corpus_root(project_root) / "audited" / "train"
    if not prepared.exists():
        prepared = corpus_root(project_root) / "prepared" / "train"
    files = sorted(prepared.rglob("*.gd")) if prepared.exists() else []
    if len(files) < 10:
        raise FileNotFoundError("Not enough validated training files yet. Run download, scan and validation first.")
    tokenizer = BPETokenizer.train(files, vocab_size=vocab_size, min_frequency=min_frequency)
    output = project_root / "artifacts" / "tokenizer_bpe_godot.json"
    tokenizer.save(output)
    sample = "extends Node\n\nfunc _ready() -> void:\n    print(\"hello\")\n"
    byte_count = len(sample.encode("utf-8"))
    token_count = len(tokenizer.encode(sample))
    report = {
        "created_at": time.time(),
        "path": output.relative_to(project_root).as_posix(),
        "vocab_size": tokenizer.vocab_size,
        "fingerprint": tokenizer.fingerprint(),
        "training_files": len(files),
        "sample_bytes": byte_count,
        "sample_tokens": token_count,
        "sample_compression": round(byte_count / max(1, token_count), 2),
    }
    _json_write(corpus_root(project_root) / "tokenizer_report.json", report)
    print(json.dumps(report, indent=2))
    return report


def status(project_root: Path) -> dict[str, Any]:
    root = corpus_root(project_root)
    registry = load_registry(project_root)

    def load(name: str) -> Any:
        path = root / name
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        except (OSError, ValueError, TypeError):
            return None

    fetch_report = load("fetch_report.json")
    fetched_by_id = {item.get("id"): item for item in (fetch_report or {}).get("sources", [])}
    downloads = []
    for source in registry["sources"]:
        path = root / "downloads" / source["id"]
        commit = _resolved_commit(path)
        metadata = _source_metadata(path) if commit else None
        license_verification = (metadata or {}).get("license_verification") if metadata else None
        if commit and (not license_verification or not license_verification.get("verified")):
            license_verification = verify_declared_license(path, source)
        ready = bool(
            commit
            and metadata
            and metadata.get("url") == source.get("url")
            and metadata.get("ref") == source.get("branch")
            and license_verification
            and license_verification.get("verified")
        )
        fetched = fetched_by_id.get(source["id"], {})
        size_bytes = fetched.get("size_bytes") if fetched.get("commit") == commit else None
        downloads.append({
            "id": source["id"],
            "downloaded": ready,
            "present": bool(commit),
            "needs_refresh": bool(commit) and not ready,
            "commit": commit,
            "ref": source.get("branch"),
            "size_mb": round(int(size_bytes) / 1024**2, 1) if size_bytes is not None else None,
            "license_verified": bool(license_verification and license_verification.get("verified")),
            "license_file": (license_verification or {}).get("file"),
        })
    processed_candidates: list[tuple[Path, dict[str, Any]]] = []
    processed_root = project_root / "data" / "processed"
    if processed_root.exists():
        for candidate in processed_root.glob("corpus*/manifest.json"):
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            if isinstance(payload, dict):
                processed_candidates.append((candidate, payload))
    processed_path: Path | None = None
    processed_payload: dict[str, Any] | None = None
    if processed_candidates:
        processed_path, processed_payload = max(
            processed_candidates,
            key=lambda item: (int(item[1].get("train_tokens") or 0), item[0].stat().st_mtime),
        )
    try:
        from .scale_plan import build_scale_plan
        scale_plan = build_scale_plan(project_root)
    except Exception:
        scale_plan = None
    try:
        from .local_sources import status as local_source_status
        local_sources = local_source_status(project_root)
    except Exception:
        local_sources = None
    return {
        "registry": registry,
        "downloads": downloads,
        "fetch": fetch_report,
        "manifest": load("corpus_manifest.json"),
        "validation": load("validation_report.json"),
        "tokenizer": load("tokenizer_report.json"),
        "audit": load("audit_report.json"),
        "processed": processed_payload,
        "processed_manifest_path": processed_path.relative_to(project_root).as_posix() if processed_path else None,
        "prepared_exists": (root / "prepared" / "train").exists(),
        "instructions": load("instruction_report.json"),
        "scale_plan": scale_plan,
        "local_sources": local_sources,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Licensed Godot corpus builder")
    parser.add_argument("--root", default=".", help="Godot Coder project root")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--source", action="append", default=[])
    fetch.add_argument("--update", action="store_true")
    sub.add_parser("build")
    validate = sub.add_parser("validate")
    validate.add_argument("--no-docs", action="store_true")
    bpe = sub.add_parser("train-bpe")
    bpe.add_argument("--vocab-size", type=int, default=8192)
    bpe.add_argument("--min-frequency", type=int, default=2)
    sub.add_parser("status")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(args.root).resolve()
    if args.command == "fetch":
        fetch_sources(root, source_ids=set(args.source) or None, update=args.update)
    elif args.command == "build":
        build_staging(root)
    elif args.command == "validate":
        validate_and_finalize(root, include_docs=not args.no_docs)
    elif args.command == "train-bpe":
        train_bpe(root, vocab_size=args.vocab_size, min_frequency=args.min_frequency)
    elif args.command == "status":
        print(json.dumps(status(root), indent=2))


if __name__ == "__main__":
    main()
