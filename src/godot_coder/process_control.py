from __future__ import annotations

import atexit
import os
import queue
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence


@dataclass(frozen=True)
class ManagedProcessResult:
    command: list[str]
    return_code: int | None
    output: str
    timed_out: bool
    duration_seconds: float
    pid: int | None
    termination_attempted: bool
    startup_error: str | None = None
    aborted: bool = False
    abort_reason: str | None = None
    idle_timed_out: bool = False


def _windows_creation_flags() -> int:
    return (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )



# ---------------------------------------------------------------------------
# Windows Job Object - guarantees that every child process (including Mono
# grandchildren spawned by Godot) is forcefully terminated, even when the
# parent Python process crashes or taskkill misses detached descendants.
# ---------------------------------------------------------------------------
_WIN_JOB_HANDLES: list[int] = []


def _windows_job_create() -> int | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None
        class _JI(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", ctypes.c_ubyte * 48),
                ("IoInfo", ctypes.c_ubyte * 16),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]
        info = _JI()
        ctypes.memset(ctypes.byref(info), 0, ctypes.sizeof(info))
        ctypes.memmove(ctypes.byref(info), (ctypes.c_uint32 * 1)(0x2000), 4)
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        if not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(handle)
            return None
        _WIN_JOB_HANDLES.append(handle)
        return handle
    except (AttributeError, OSError):
        return None


def _windows_job_assign(job_handle: int, pid: int) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        rights = 0x00100000 | 0x0040 | 0x0002
        ph = kernel32.OpenProcess(rights, False, pid)
        if not ph:
            return False
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        ok = bool(kernel32.AssignProcessToJobObject(wintypes.HANDLE(job_handle), ph))
        kernel32.CloseHandle(ph)
        return ok
    except (AttributeError, OSError):
        return False


def _windows_job_terminate(job_handle: int) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        return bool(kernel32.TerminateJobObject(wintypes.HANDLE(job_handle), 1))
    except (AttributeError, OSError):
        return False


def _windows_job_close(job_handle: int) -> None:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle(wintypes.HANDLE(job_handle))
        if job_handle in _WIN_JOB_HANDLES:
            _WIN_JOB_HANDLES.remove(job_handle)
    except Exception:
        pass


def _cleanup_remaining_jobs() -> None:
    for h in list(_WIN_JOB_HANDLES):
        _windows_job_close(h)


atexit.register(_cleanup_remaining_jobs)





def _posix_descendants(root_pid: int) -> list[int]:
    children: dict[int, list[int]] = {}
    proc = Path("/proc")
    if not proc.is_dir():
        return []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text(encoding="utf-8", errors="replace").split()
            pid = int(fields[0])
            parent = int(fields[3])
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(parent, []).append(pid)
    result: list[int] = []
    stack = list(children.get(root_pid, []))
    while stack:
        pid = stack.pop()
        result.append(pid)
        stack.extend(children.get(pid, []))
    return result

def _windows_process_handle(pid: int, access: int):
    """Open a Windows process handle, returning None when the process is already gone."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(access, False, pid)
        return (kernel32, handle) if handle else None
    except (AttributeError, OSError):
        return None


def _windows_wait_for_exit(pid: int, wait_seconds: float, opened=None) -> bool:
    """Wait for the exact Windows process object, not localized tasklist output."""
    try:
        import ctypes
        from ctypes import wintypes

        synchronize = 0x00100000
        query_limited = 0x1000
        owned = opened is None
        opened = opened or _windows_process_handle(pid, synchronize | query_limited)
        if opened is None:
            return True
        kernel32, handle = opened
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        milliseconds = max(1, min(0xFFFFFFFE, int(max(0.0, wait_seconds) * 1000)))
        result = kernel32.WaitForSingleObject(handle, milliseconds)
        if owned:
            kernel32.CloseHandle(handle)
        return result == 0  # WAIT_OBJECT_0
    except (AttributeError, OSError):
        return False


def _terminate_windows_process_tree(pid: int, *, force: bool, wait_seconds: float, job_handle: int | None = None) -> bool:
    if job_handle is not None:
        _windows_job_terminate(job_handle)
        deadline = time.monotonic() + max(0.1, wait_seconds)
        while time.monotonic() < deadline:
            if not process_is_alive(pid):
                return True
            time.sleep(0.05)
        return not process_is_alive(pid)
    # Fallback: traditional taskkill
    # Keep a native handle open before taskkill. This prevents false success from
    # localized/racy tasklist output and lets us wait for the exact process object.
    synchronize = 0x00100000
    query_limited = 0x1000
    opened = _windows_process_handle(pid, synchronize | query_limited)
    if opened is None:
        return True
    kernel32, handle = opened
    command = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        command.append("/F")
    try:
        subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(2.0, wait_seconds),
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return _windows_wait_for_exit(pid, wait_seconds, opened=opened)
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        try:
            kernel32.CloseHandle(handle)
        except Exception:
            pass


def terminate_process_tree(pid: int, *, force: bool = False, wait_seconds: float = 5.0, job_handle: int | None = None) -> bool:
    """Terminate only the supplied process tree. Returns True only after the root exited."""
    if pid <= 0:
        return True
    if os.name == "nt":
        return _terminate_windows_process_tree(pid, force=force, wait_seconds=wait_seconds, job_handle=job_handle)
    selected_signal = signal.SIGKILL if force else signal.SIGTERM
    descendants = _posix_descendants(pid)
    for child_pid in descendants:
        try:
            os.kill(child_pid, selected_signal)
        except (ProcessLookupError, PermissionError):
            continue
    try:
        os.killpg(pid, selected_signal)
    except ProcessLookupError:
        if not descendants:
            return True
    except PermissionError:
        try:
            os.kill(pid, selected_signal)
        except (ProcessLookupError, PermissionError):
            return False
    deadline = time.monotonic() + max(0.1, wait_seconds)
    while time.monotonic() < deadline:
        if not process_is_alive(pid):
            return True
        time.sleep(0.05)
    return not process_is_alive(pid)


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        opened = _windows_process_handle(pid, 0x1000)  # PROCESS_QUERY_LIMITED_INFORMATION
        if opened is None:
            return False
        try:
            import ctypes
            from ctypes import wintypes

            kernel32, handle = opened
            exit_code = wintypes.DWORD()
            kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            ok = bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)))
            return ok and exit_code.value == 259  # STILL_ACTIVE
        finally:
            try:
                opened[0].CloseHandle(opened[1])
            except Exception:
                pass
    try:
        stat_path = Path("/proc") / str(pid) / "stat"
        if stat_path.is_file():
            fields = stat_path.read_text(encoding="utf-8", errors="replace").split()
            if len(fields) > 2 and fields[2] == "Z":
                return False
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_command_line(pid: int) -> str | None:
    if pid <= 0:
        return None
    if os.name == "nt":
        script = (
            "$p = Get-CimInstance Win32_Process -Filter \"ProcessId = %d\"; "
            "if ($null -ne $p) { [Console]::Out.Write($p.CommandLine) }" % pid
        )
        for executable in ("powershell.exe", "pwsh.exe"):
            try:
                completed = subprocess.run(
                    [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if completed.returncode == 0 and completed.stdout.strip():
                return completed.stdout.strip()
        return None
    path = Path("/proc") / str(pid) / "cmdline"
    try:
        return path.read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace").strip() or None
    except OSError:
        return None


def run_managed_process(
    command: Sequence[str],
    *,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float,
    heartbeat_seconds: float = 5.0,
    on_line: Callable[[str], None] | None = None,
    on_heartbeat: Callable[[float, str | None], None] | None = None,
    on_start: Callable[[int], None] | None = None,
    abort_on_line: Callable[[str], str | None] | None = None,
    idle_timeout_seconds: float | None = None,
    max_output_chars: int = 4_000_000,
) -> ManagedProcessResult:
    """Run a command with streamed output, heartbeats, timeout, and process-tree cleanup."""
    command_list = [str(value) for value in command]
    started = time.monotonic()
    creation_flags = _windows_creation_flags() if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            command_list,
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creation_flags,
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        return ManagedProcessResult(
            command=command_list,
            return_code=None,
            output="",
            timed_out=False,
            duration_seconds=time.monotonic() - started,
            pid=None,
            termination_attempted=False,
            startup_error=f"{type(exc).__name__}: {exc}",
        )

    job_handle: int | None = None
    try:
        job_handle = _windows_job_create()
        if job_handle is not None and process.pid is not None:
            _windows_job_assign(job_handle, process.pid)
    except Exception:
        job_handle = None
    if on_start is not None:
        on_start(process.pid)

    output_queue: queue.Queue[object] = queue.Queue()
    reader_done = object()
    queue_empty = object()

    def _read_output() -> None:
        try:
            assert process.stdout is not None
            for raw_line in process.stdout:
                output_queue.put(raw_line.rstrip("\r\n"))
        finally:
            output_queue.put(reader_done)

    reader = threading.Thread(target=_read_output, daemon=True)
    reader.start()
    output_parts: list[str] = []
    output_chars = 0
    last_line: str | None = None
    reader_finished = False
    timed_out = False
    idle_timed_out = False
    aborted = False
    abort_reason: str | None = None
    termination_attempted = False
    last_output_at = started
    next_heartbeat = started + max(0.2, heartbeat_seconds)
    timeout_at = started + max(0.1, timeout_seconds)

    while True:
        now = time.monotonic()
        idle_deadline = (last_output_at + max(0.1, idle_timeout_seconds)) if idle_timeout_seconds is not None else None
        if now >= timeout_at and process.poll() is None:
            timed_out = True
            termination_attempted = True
            terminate_process_tree(process.pid, force=False, wait_seconds=3.0, job_handle=job_handle)
            if process.poll() is None:
                terminate_process_tree(process.pid, force=True, wait_seconds=5.0, job_handle=job_handle)
        elif idle_deadline is not None and now >= idle_deadline and process.poll() is None:
            timed_out = True
            idle_timed_out = True
            termination_attempted = True
            abort_reason = f"no process output for {idle_timeout_seconds:.1f} seconds"
            terminate_process_tree(process.pid, force=False, wait_seconds=3.0, job_handle=job_handle)
            if process.poll() is None:
                terminate_process_tree(process.pid, force=True, wait_seconds=5.0, job_handle=job_handle)
        wait_for = min(0.2, max(0.0, next_heartbeat - now), max(0.0, timeout_at - now))
        try:
            item = output_queue.get(timeout=max(0.01, wait_for))
        except queue.Empty:
            item = queue_empty
        if item is reader_done:
            reader_finished = True
        elif item is not queue_empty:
            line = str(item)
            last_line = line
            last_output_at = time.monotonic()
            encoded_length = len(line) + 1
            output_parts.append(line)
            output_chars += encoded_length
            while output_parts and output_chars > max_output_chars:
                removed = output_parts.pop(0)
                output_chars -= len(removed) + 1
            if on_line is not None:
                on_line(line)
            if abort_on_line is not None and process.poll() is None:
                requested_reason = abort_on_line(line)
                if requested_reason:
                    aborted = True
                    abort_reason = str(requested_reason)[:1000]
                    termination_attempted = True
                    terminate_process_tree(process.pid, force=False, wait_seconds=3.0, job_handle=job_handle)
                    if process.poll() is None:
                        terminate_process_tree(process.pid, force=True, wait_seconds=5.0, job_handle=job_handle)
        now = time.monotonic()
        if on_heartbeat is not None and now >= next_heartbeat and process.poll() is None:
            on_heartbeat(now - started, last_line)
            next_heartbeat = now + max(0.2, heartbeat_seconds)
        if process.poll() is not None and reader_finished and output_queue.empty():
            break
        if (timed_out or aborted) and process.poll() is not None and reader_finished:
            break

    try:
        return_code = process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        termination_attempted = True
        terminate_process_tree(process.pid, force=True, wait_seconds=3.0, job_handle=job_handle)
        return_code = process.poll()
    reader.join(timeout=1)
    if job_handle is not None:
        _windows_job_close(job_handle)
    return ManagedProcessResult(
        command=command_list,
        return_code=return_code,
        output="\n".join(output_parts),
        timed_out=timed_out,
        duration_seconds=time.monotonic() - started,
        pid=process.pid,
        termination_attempted=termination_attempted,
        startup_error=None,
        aborted=aborted,
        abort_reason=abort_reason,
        idle_timed_out=idle_timed_out,
    )
