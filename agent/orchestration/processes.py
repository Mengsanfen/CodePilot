"""Terminate only child process trees created by this worker."""
from __future__ import annotations

import os
import signal
import subprocess


def process_options() -> dict:
    if os.name == "nt":
        # Suspend until attached to a kill-on-close Job Object: children cannot escape
        # by spawning before the supervisor has established ownership.
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | 0x4}
    return {"start_new_session": True}


def stop_process_tree(process: subprocess.Popen):
    if os.name == "nt":
        handle = getattr(process, '_codepilot_job', None)
        if handle:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
            process._codepilot_job = None
        elif process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def attach_process_tree(process: subprocess.Popen):
    if os.name != 'nt':
        return
    import ctypes as c
    from ctypes import wintypes as w

    class Basic(c.Structure):
        _fields_ = [('ProcessTime', c.c_int64), ('JobTime', c.c_int64), ('Flags', w.DWORD),
                    ('MinSet', c.c_size_t), ('MaxSet', c.c_size_t), ('ActiveLimit', w.DWORD),
                    ('Affinity', c.c_size_t), ('Priority', w.DWORD), ('Scheduling', w.DWORD)]
    class Io(c.Structure):
        _fields_ = [(name, c.c_uint64) for name in ('ReadOps', 'WriteOps', 'OtherOps', 'ReadBytes', 'WriteBytes', 'OtherBytes')]
    class Extended(c.Structure):
        _fields_ = [('Basic', Basic), ('Io', Io), ('ProcessMemory', c.c_size_t),
                    ('JobMemory', c.c_size_t), ('PeakProcess', c.c_size_t), ('PeakJob', c.c_size_t)]
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise c.WinError(c.get_last_error())
    try:
        limits = Extended()
        limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(handle, 9, c.byref(limits), c.sizeof(limits)):
            raise c.WinError(c.get_last_error())
        if not kernel.AssignProcessToJobObject(handle, w.HANDLE(int(process._handle))):
            raise c.WinError(c.get_last_error())
        resume = c.WinDLL('ntdll').NtResumeProcess
        resume.argtypes = [w.HANDLE]
        resume.restype = c.c_long
        if resume(w.HANDLE(int(process._handle))) != 0:
            raise RuntimeError('could not resume supervised child')
        process._codepilot_job = handle
    except BaseException:
        kernel.CloseHandle(handle)
        process.kill()
        raise
