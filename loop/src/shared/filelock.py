"""An exclusive advisory file lock that works on POSIX and Windows and is released when the process dies."""

from __future__ import annotations

import os
import sys
import time
from typing import IO

if sys.platform == "win32":
    import errno
    import msvcrt

    _CONTENDED = (errno.EACCES, errno.EDEADLK)
    _POLL_S = 0.05

    def lock_file(handle: IO[str] | IO[bytes], *, wait: bool = True) -> None:
        """Takes an exclusive lock on `handle`; raises BlockingIOError when `wait` is unset and another handle holds it."""
        descriptor = handle.fileno()
        while True:
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                return
            except OSError as exception:
                if exception.errno not in _CONTENDED:
                    raise
                if not wait:
                    raise BlockingIOError(*exception.args) from exception
                time.sleep(_POLL_S)

    def unlock_file(handle: IO[str] | IO[bytes]) -> None:
        """Releases the lock `lock_file` took on `handle`."""
        descriptor = handle.fileno()
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def lock_file(handle: IO[str] | IO[bytes], *, wait: bool = True) -> None:
        """Takes an exclusive lock on `handle`; raises BlockingIOError when `wait` is unset and another handle holds it."""
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))

    def unlock_file(handle: IO[str] | IO[bytes]) -> None:
        """Releases the lock `lock_file` took on `handle`."""
        fcntl.flock(handle, fcntl.LOCK_UN)
