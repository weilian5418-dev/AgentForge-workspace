"""Single-instance lock and runtime record (BE-03.3)."""

from __future__ import annotations

import json
import os
import signal
import socket
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from agentforge.core.config import Settings
from agentforge.core.exit_codes import CONFLICT


class InstanceError(RuntimeError):
    def __init__(self, message: str, *, exit_code: int = CONFLICT) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass
class PlatformInstance:
    """Managed AgentForge API/control-plane process instance.

    Distinct from ``AgentInstance`` (role execution sessions owned by adapters).
    """

    instance_id: str
    pid: int
    started_at: str
    host: str
    port: int
    run_mode: str
    stage: str
    version: str
    components: list[dict[str, str]]
    process_key: str = ""
    kind: str = "platform"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


# Backward-compatible alias — prefer PlatformInstance in new code.
RuntimeRecord = PlatformInstance


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _lock_path(settings: Settings) -> Path:
    return settings.runtime_dir / "instance.lock"


def _windows_process_handle(pid: int) -> Any | None:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return None
    return handle


def _windows_close_handle(handle: Any) -> None:
    import ctypes

    ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)


def _windows_process_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    handle = _windows_process_handle(pid)
    if handle is None:
        # 5 = ERROR_ACCESS_DENIED → process exists but is not queryable.
        return ctypes.get_last_error() == 5
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        still_active = 259
        return int(code.value) == still_active
    finally:
        _windows_close_handle(handle)


def _windows_process_key(pid: int) -> str | None:
    import ctypes
    from ctypes import wintypes

    handle = _windows_process_handle(pid)
    if handle is None:
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        )
        if not ok:
            return None
        value = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        return f"win:{pid}:{value}"
    finally:
        _windows_close_handle(handle)


def _posix_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _posix_process_key(pid: int) -> str | None:
    if not _posix_process_alive(pid):
        return None
    stat_path = Path(f"/proc/{pid}/stat")
    if stat_path.exists():
        try:
            # Field 22 (1-based) is starttime after the comm field in parentheses.
            raw = stat_path.read_text(encoding="utf-8")
            after_comm = raw.rsplit(")", 1)[-1].split()
            starttime = after_comm[19]
            return f"linux:{pid}:{starttime}"
        except (OSError, IndexError, ValueError):
            pass
    # Non-Linux POSIX: identity requires a recorded key; callers treat mismatch as unknown.
    return f"posix:{pid}"


def current_process_key(pid: int | None = None) -> str:
    """Stable OS identity fingerprint for the given (or current) process."""
    target = os.getpid() if pid is None else pid
    if os.name == "nt":
        key = _windows_process_key(target)
    else:
        key = _posix_process_key(target)
    if key is None:
        raise InstanceError(f"Could not fingerprint process pid={target}", exit_code=CONFLICT)
    return key


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_alive(pid)
    return _posix_process_alive(pid)


def read_runtime_record(settings: Settings) -> PlatformInstance | None:
    path = settings.runtime_record_path
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return PlatformInstance(
            instance_id=data["instanceId"],
            pid=int(data["pid"]),
            started_at=data["startedAt"],
            host=data["host"],
            port=int(data["port"]),
            run_mode=data["runMode"],
            stage=data["stage"],
            version=data["version"],
            components=list(data.get("components", [])),
            process_key=str(data.get("processKey") or ""),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def write_runtime_record(settings: Settings, record: PlatformInstance) -> None:
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "instanceId": record.instance_id,
        "pid": record.pid,
        "startedAt": record.started_at,
        "host": record.host,
        "port": record.port,
        "runMode": record.run_mode,
        "stage": record.stage,
        "version": record.version,
        "components": record.components,
        "processKey": record.process_key,
    }
    path = settings.runtime_record_path
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def clear_runtime_record(settings: Settings) -> None:
    for path in (settings.runtime_record_path, _lock_path(settings)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(
        socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM
    ) as sock:
        sock.settimeout(0.5)
        try:
            return sock.connect_ex((host, port)) == 0
        except OSError:
            return False


class InstanceLock:
    """Hold an exclusive lock file for the process lifetime."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._fh: Any | None = None
        self.instance_id = f"inst_{uuid.uuid4().hex}"
        self.started_at = _iso_now()
        self.process_key = current_process_key()

    def acquire(self) -> None:
        self.settings.runtime_dir.mkdir(parents=True, exist_ok=True)
        existing = read_runtime_record(self.settings)
        if existing is not None and matches_managed_process(existing):
            raise InstanceError(
                f"Instance already running: {existing.instance_id} pid={existing.pid} "
                f"port={existing.port}",
                exit_code=CONFLICT,
            )
        if existing is not None:
            clear_runtime_record(self.settings)

        if port_in_use(self.settings.host, self.settings.port):
            raise InstanceError(
                f"Port {self.settings.port} is already in use by another program",
                exit_code=CONFLICT,
            )

        lock = _lock_path(self.settings)
        self._fh = open(lock, "a+b")  # noqa: SIM115
        if self._fh.tell() == 0:
            self._fh.write(b"\0")
            self._fh.flush()
        self._fh.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl as _fcntl

                posix_lock = cast(Any, _fcntl)
                posix_lock.flock(self._fh.fileno(), posix_lock.LOCK_EX | posix_lock.LOCK_NB)
        except OSError as exc:
            self.release()
            raise InstanceError("Could not acquire instance lock", exit_code=CONFLICT) from exc

        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(f"{self.instance_id}\n{os.getpid()}\n{self.started_at}\n".encode())
        self._fh.flush()

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl as _fcntl

                posix_lock = cast(Any, _fcntl)
                posix_lock.flock(self._fh.fileno(), posix_lock.LOCK_UN)
        except OSError:
            pass
        try:
            self._fh.close()
        except OSError:
            pass
        self._fh = None


def matches_managed_process(record: PlatformInstance) -> bool:
    """Require live PID and matching OS process identity — never kill by port alone."""
    if not record.process_key or record.pid <= 0:
        return False
    if not _process_alive(record.pid):
        return False
    if os.name == "nt":
        live_key = _windows_process_key(record.pid)
    else:
        live_key = _posix_process_key(record.pid)
    return live_key is not None and live_key == record.process_key


def force_terminate_process(record: PlatformInstance) -> bool:
    """Last-resort kill after identity check. Returns True if a terminate was attempted."""
    if not matches_managed_process(record):
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        process_terminate = 0x0001
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(process_terminate, False, record.pid)
        if not handle:
            return False
        try:
            return bool(kernel32.TerminateProcess(handle, 1))
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(record.pid, signal.SIGTERM if hasattr(signal, "SIGTERM") else signal.SIGINT)
    except OSError:
        return False
    return True


def wait_until(predicate: Any, *, timeout_seconds: float, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False
