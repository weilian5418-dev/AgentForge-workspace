"""Managed process-tree lifetime for explicitly authorized local checks (not a filesystem sandbox)."""

from __future__ import annotations

import os
import subprocess
from typing import Any, cast


class ProcessTree:
    def __init__(self) -> None:
        self.job: Any = None
        self.kernel: Any = None
        self.process: subprocess.Popen[bytes] | None = None

    def start(self, argv: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        if os.name != "nt":
            self.process = subprocess.Popen(argv, start_new_session=True, **kwargs)
            return self.process
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        for name in ["CreateJobObjectW"]:
            getattr(k, name).restype = wintypes.HANDLE
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel = k
        self.job = k.CreateJobObjectW(None, None)
        if not self.job:
            raise OSError("Cannot establish check process boundary")
        proc = subprocess.Popen(
            argv, creationflags=0x00000004, **kwargs
        )  # suspended until attached
        self.process = proc
        handle = cast(Any, proc)._handle
        if not k.AssignProcessToJobObject(self.job, handle):
            proc.kill()
            self.close()
            raise OSError("Cannot bind check process tree")
        nt = ctypes.WinDLL("ntdll")
        nt.NtResumeProcess.argtypes = [wintypes.HANDLE]
        if nt.NtResumeProcess(handle) != 0:
            self.terminate()
            self.close()
            raise OSError("Cannot resume managed check")
        return proc

    def quiescent(self) -> bool:
        if self.process is None or self.process.poll() is None:
            return False
        if os.name == "nt":
            import ctypes

            class Accounting(ctypes.Structure):
                _fields_ = [
                    ("user", ctypes.c_int64),
                    ("kernel", ctypes.c_int64),
                    ("period_user", ctypes.c_int64),
                    ("period_kernel", ctypes.c_int64),
                    ("faults", ctypes.c_uint32),
                    ("total", ctypes.c_uint32),
                    ("active", ctypes.c_uint32),
                    ("terminated", ctypes.c_uint32),
                ]

            info = Accounting()
            return bool(
                self.kernel.QueryInformationJobObject(
                    self.job, 1, ctypes.byref(info), ctypes.sizeof(info), None
                )
                and info.active == 0
            )
        try:
            cast(Any, os).killpg(self.process.pid, 0)
        except ProcessLookupError:
            return True
        return False

    def terminate(self) -> None:
        if self.process is None:
            return
        if self.job:
            self.kernel.TerminateJobObject(self.job, 1)
        elif os.name != "nt":
            import signal

            try:
                cast(Any, os).killpg(self.process.pid, cast(Any, signal).SIGKILL)
            except ProcessLookupError:
                pass

    def close(self) -> None:
        if self.job:
            self.kernel.CloseHandle(self.job)
            self.job = None
