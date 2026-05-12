"""
Keep long-running CLI jobs from being suspended by idle system sleep.

On Windows this uses SetThreadExecutionState for the lifetime of the context.
On other platforms it is a no-op so the scripts remain portable.
"""

from __future__ import annotations

import contextlib
import ctypes
import os


_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


@contextlib.contextmanager
def keep_system_awake(label: str = "job"):
    if os.name != "nt":
        yield
        return

    flags = _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED
    result = ctypes.windll.kernel32.SetThreadExecutionState(flags)
    if result:
        print(f"[Power] Keeping system awake during {label}.")
    else:
        print(f"[Power] Could not enable keep-awake mode for {label}.")

    try:
        yield
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
        if result:
            print(f"[Power] Released keep-awake mode for {label}.")
