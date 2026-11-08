import errno
import itertools
import logging
import os
import tempfile
import re
import shutil
import zipfile
from pathlib import Path
from collections.abc import Iterable

from unidecode import unidecode
from pathvalidate import validate_filename, ValidationError as FilenameValidationError

from kptncook.http_errors import UserFacingError
from kptncook.models import Image, RecipeStep, StepTimer, localized_fallback

logger = logging.getLogger(__name__)

TIMER_PLACEHOLDER = "<timer>"
TIMER_PLACEHOLDER_PATTERN = re.compile(
    rf"{re.escape(TIMER_PLACEHOLDER)}(?P<punct>[.!?])?"
)


def format_timer(timer: StepTimer) -> str:
    """Convert timer to human-readable German string (e.g. '15 Min.', '30–40 Min.')."""
    if timer.min_or_exact is not None and timer.max is not None:
        return f"{timer.min_or_exact}–{timer.max} Min."
    if timer.min_or_exact is not None:
        return f"{timer.min_or_exact} Min."
    if timer.max is not None:
        return f"bis zu {timer.max} Min."
    return ""


def expand_timer_placeholders(text: str, timers: list[StepTimer] | None) -> str:
    """Replace <timer> placeholders with formatted timer values by position."""
    if not text:
        return text
    if not timers:
        return text.replace(TIMER_PLACEHOLDER, "")
    timer_index = [0]

    def replacer(match: re.Match[str]) -> str:
        idx = timer_index[0]
        timer_index[0] += 1
        punctuation = match.group("punct") or ""
        if idx < len(timers):
            timer_text = format_timer(timers[idx])
            if punctuation == "." and timer_text.endswith("."):
                timer_text = timer_text[:-1]
            return f"{timer_text}{punctuation}"
        return punctuation

    return TIMER_PLACEHOLDER_PATTERN.sub(replacer, text)


def get_step_text(step: RecipeStep, language: str = "de") -> str:
    """Get localized step text with timer placeholders expanded."""
    raw = localized_fallback(step.title, language) or ""
    return expand_timer_placeholders(raw, step.timers or [])


ZipContent = bytes | str | Path


def asciify_string(s: str) -> str:
    s = unidecode(s)
    s = re.sub(r"[^\w\s]", "_", s)
    s = re.sub(r"\s+", "_", s)
    return s


def get_cover(image_list: list[Image] | None) -> Image | None:
    if not isinstance(image_list, list):
        return None
    covers = [image for image in image_list if image.type == "cover"]
    if len(covers) != 1:
        return None
    return covers[0]


def replace_timers_in_step(step, text: str) -> str:
    """Replace ``<timer>`` placeholders in ``text`` with the step's timers.

    Timers are consumed in order, retaining exact, max-only and ranged minutes. A
    placeholder with no remaining timer is left untouched. The original
    ``step.timers`` list is not mutated.
    """
    timer_iter = iter(getattr(step, "timers", None) or [])

    def repl(match: re.Match[str]) -> str:
        timer = next(timer_iter, None)
        if timer is None:
            return match.group(0)
        if timer.min_or_exact is not None and timer.max is not None:
            return f"{timer.min_or_exact}–{timer.max}m"
        if timer.min_or_exact is not None:
            return f"{timer.min_or_exact}m"
        if timer.max is not None:
            return f"≤{timer.max}m"
        return match.group(0)

    return re.sub(r"<timer>", repl, text)


def validate_export_id(identifier: str) -> None:
    """Admit opaque IDs as portable single components before export writes."""
    error = "Recipe identifier is not a safe export filename component"
    if identifier in {".", ".."}:
        raise UserFacingError(error)
    try:
        validate_filename(identifier, platform="universal", max_len=128)
    except FilenameValidationError:
        raise UserFacingError(error) from None


# errno values ``link()`` reports when the filesystem cannot hard-link at all
# (FAT/exFAT, some SMB/FUSE mounts), as opposed to ordinary failures such as a
# too-long name or a full disk.
_LINK_UNSUPPORTED_ERRNOS = frozenset(
    code
    for code in (
        errno.EPERM,
        errno.EXDEV,
        errno.ENOSYS,
        getattr(errno, "ENOTSUP", None),
        getattr(errno, "EOPNOTSUPP", None),
    )
    if code is not None
)


def _hard_links_unsupported(directory: Path, reason: str) -> UserFacingError:
    return UserFacingError(
        f"Could not publish the export in {directory}: the destination must "
        f"support hard links ({reason}). Choose a different export location."
    )


def move_to_free_target(
    source: str | Path,
    directory: str | Path,
    stem: str,
    extension: str,
    disambiguator: str | None = None,
) -> Path:
    """Move ``source`` into ``directory`` without overwriting an existing file.

    The first candidate is ``<stem><extension>``. If that name is taken, the
    ``disambiguator`` (for example a recipe id) is appended, then a counter:
    ``<stem>-<disambiguator><extension>``, ``<stem>-<disambiguator>-2<extension>``
    and so on (``<stem>-2<extension>`` ... without a disambiguator). The target
    name is published by linking a flushed sibling staging file. Hard-link
    support is required: publication fails safely when unavailable. Existing
    files are never replaced and final names always contain complete data.
    """
    directory = Path(directory)
    base = f"{stem}-{disambiguator}" if disambiguator else stem
    candidates = itertools.chain(
        [stem],
        [base] if disambiguator else [],
        (f"{base}-{counter}" for counter in itertools.count(2)),
    )
    # Copy first: the source may be on a different filesystem. Only the sibling
    # staging inode is linked, so publication itself is atomic and no-clobber.
    staging_path: Path | None = None
    try:
        with open(source, "rb") as src:
            with tempfile.NamedTemporaryFile(
                dir=directory, prefix=".kptncook-", suffix=".tmp", delete=False
            ) as staged:
                staging_path = Path(staged.name)
                shutil.copyfileobj(src, staged)
                staged.flush()
                os.fsync(staged.fileno())
        for candidate in candidates:
            target = directory / f"{candidate}{extension}"
            try:
                link = getattr(os, "link", None)
                if link is None:
                    raise NotImplementedError("os.link is unavailable")
                link(staging_path, target)
            except FileExistsError:
                continue
            except NotImplementedError as exc:
                raise _hard_links_unsupported(directory, str(exc)) from exc
            except OSError as exc:
                if exc.errno in _LINK_UNSUPPORTED_ERRNOS:
                    raise _hard_links_unsupported(
                        directory, exc.strerror or str(exc)
                    ) from exc
                raise UserFacingError(
                    f"Could not publish the export as {target}: {exc.strerror or exc}."
                ) from exc
            # Publication succeeded. Cleanup errors must not make callers undo
            # assets referenced by an already published Markdown note.
            try:
                Path(source).unlink()
            except OSError:
                logger.warning("Export published; could not remove staging source")
            return target
    finally:
        if staging_path is not None:
            try:
                staging_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove export staging file")
    raise AssertionError("unreachable")  # pragma: no cover


def write_zip(zip_path: Path, entries: Iterable[tuple[str, ZipContent]]) -> None:
    with zipfile.ZipFile(
        zip_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
    ) as zip_file:
        for arcname, content in entries:
            if isinstance(content, Path):
                zip_file.write(content, arcname=arcname)
            else:
                zip_file.writestr(arcname, content)
