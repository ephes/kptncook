"""Directory sync with a narrow portability fallback, never hiding disk faults."""

import errno
import os
from pathlib import Path

_UNSUPPORTED = {errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP}


def fsync_directory(directory: Path) -> None:
    """Sync directory entries where supported; propagate genuine I/O failures."""
    try:
        fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError as exc:
        if exc.errno in _UNSUPPORTED or (os.name == "nt" and exc.errno == errno.EACCES):
            return
        raise
    try:
        try:
            os.fsync(fd)
        except OSError as exc:
            if exc.errno not in _UNSUPPORTED:
                raise
    finally:
        os.close(fd)
