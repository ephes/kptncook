"""Directory sync with a narrow portability fallback, never hiding disk faults."""

from contextlib import suppress
import errno
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

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
        try:
            os.close(fd)
        except OSError:
            with suppress(Exception):
                logger.warning("Could not close directory sync descriptor.")
