from __future__ import annotations

import argparse
import ipaddress
import threading
import webbrowser

import uvicorn

from . import __version__
from .remote_access import DEFAULT_REMOTE_PORT, load_remote_config
from .ui.paths import find_project_root
from .ui.server import create_app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch the local Godot Coder Studio.")
    parser.add_argument("--root", default=None, help="Project root containing pyproject.toml")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None, help="Studio port; defaults to the configured Remote Studio port or 8765")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--allow-non-loopback", action="store_true", help="Explicitly allow a non-loopback bind; not recommended with Secure Remote Studio")
    return parser.parse_args()


def is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def main() -> None:
    args = parse_args()
    root = find_project_root(args.root)
    remote_config = load_remote_config(root)
    remote_enabled = bool(remote_config.get("enabled"))
    port = int(args.port or remote_config.get("port") or DEFAULT_REMOTE_PORT)
    if remote_enabled and not is_loopback_host(args.host) and not args.allow_non_loopback:
        raise SystemExit(
            "Secure Remote Studio stays bound to localhost for security reasons. "
            "Use Tailscale Serve or explicitly allow an external bind with --allow-non-loopback."
        )
    app = create_app(root)
    url = f"http://{args.host}:{port}"
    print(f"Godot Coder Studio {__version__}")
    print(f"Project: {root}")
    print(f"Studio:  {url}")
    print("Keep this window open while using the app. Press Ctrl+C to stop it.")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
