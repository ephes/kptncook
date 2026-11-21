"""Directory sync with a narrow portability fallback, never hiding disk faults."""

from contextlib import suppress
import errno
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_UNSUPPORTED = {errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP}


def fsync_directory(directory: Path, *, skip_unreadable: bool = False) -> None:
    """Sync directory entries where supported; propagate genuine I/O failures.

    ``skip_unreadable`` tolerates permission errors when opening the directory.
    Use it only for pre-existing ancestors (for example a mode 0711 home
    directory) whose links this tool did not create; sync errors still raise.
    """
    try:
        fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError as exc:
        if exc.errno in _UNSUPPORTED or (os.name == "nt" and exc.errno == errno.EACCES):
            return
        if skip_unreadable and exc.errno in {errno.EACCES, errno.EPERM}:
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
