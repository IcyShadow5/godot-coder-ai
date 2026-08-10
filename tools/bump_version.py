"""Bump every place that names the release version in one go.

Keeps four markers in sync:
  pyproject.toml                     version = "x.y.z"
  src/godot_coder/__init__.py        __version__ = "x.y.z"
  src/godot_coder/ui/static/sw.js    CACHE_NAME "...-vx.y.z-1"
  docs/STUDIO.md                     # Godot Coder Studio vx.y.z

The STUDIO.md header is the one that kept getting forgotten, so it lives
in the same place as the others now instead of relying on memory.

Usage:
  python tools/bump_version.py 0.10.22   # bump all four markers
  python tools/bump_version.py --check   # verify all markers agree
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

MARKERS: list[tuple[str, re.Pattern[str]]] = [
    ("pyproject.toml", re.compile(r'^version = "([0-9.]+)"', re.M)),
    ("src/godot_coder/__init__.py", re.compile(r'^__version__ = "([0-9.]+)"', re.M)),
    ("src/godot_coder/ui/static/sw.js", re.compile(r"godot-coder-shell-v([0-9.]+)-1")),
    ("docs/STUDIO.md", re.compile(r"^# Godot Coder Studio v([0-9.]+)$", re.M)),
]


def read_marker(rel_path: str, pattern: re.Pattern[str]) -> tuple[str, str]:
    text = (REPO / rel_path).read_text(encoding="utf-8")
    match = pattern.search(text)
    if match is None:
        raise SystemExit(f"version marker not found in {rel_path}")
    return text, match.group(1)


def collect() -> dict[str, str]:
    return {rel: read_marker(rel, pattern)[1] for rel, pattern in MARKERS}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("new_version", nargs="?", help="target version, e.g. 0.10.22")
    parser.add_argument("--check", action="store_true", help="only verify that all markers agree")
    args = parser.parse_args()

    current = read_marker("pyproject.toml", MARKERS[0][1])[1]
    found = collect()
    mismatches = {rel: version for rel, version in found.items() if version != current}
    if mismatches:
        for rel, version in mismatches.items():
            print(f"MISMATCH {rel}: {version} != {current}")
        raise SystemExit("version markers are out of sync - fix before bumping")
    print(f"all markers agree on {current}")

    if args.check:
        return

    new_version = args.new_version or ""
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", new_version):
        raise SystemExit("new version must look like 0.10.22")
    if new_version == current:
        raise SystemExit("new version equals the current version")

    for rel_path, pattern in MARKERS:
        path = REPO / rel_path
        if rel_path.endswith("sw.js"):
            text = path.read_text(encoding="utf-8")
            old = f"godot-coder-shell-v{current}-1"
            new = f"godot-coder-shell-v{new_version}-1"
            if text.count(old) != 1:
                raise SystemExit(f"unexpected sw.js cache marker in {rel_path}")
            path.write_text(text.replace(old, new), encoding="utf-8")
        else:
            text = path.read_text(encoding="utf-8")

            def swap(match: re.Match[str]) -> str:
                return match.group(0).replace(current, new_version)

            updated, count = pattern.subn(swap, text)
            if count != 1:
                raise SystemExit(f"expected exactly one marker in {rel_path}, found {count}")
            path.write_text(updated, encoding="utf-8")
        print(f"bumped {rel_path} -> {new_version}")

    after = collect()
    bad = {rel: version for rel, version in after.items() if version != new_version}
    if bad:
        raise SystemExit(f"bump validation failed: {bad}")
    print(f"bump ok - all markers on {new_version}")


if __name__ == "__main__":
    main()
