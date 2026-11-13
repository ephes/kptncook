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
# Markdown/HTML-rendered targets would treat a bare ``<timer>`` as an unknown
# tag and hide it; a code span keeps the unresolved marker visible.
UNRESOLVED_TIMER_MARKUP = f"`{TIMER_PLACEHOLDER}`"


def format_timer(timer: StepTimer) -> str:
    """Convert timer to human-readable German string (e.g. '15 Min.', '30–40 Min.')."""
    if timer.min_or_exact is not None and timer.max is not None:
        return f"{timer.min_or_exact}–{timer.max} Min."
    if timer.min_or_exact is not None:
        return f"{timer.min_or_exact} Min."
    if timer.max is not None:
        return f"bis zu {timer.max} Min."
    return ""


def expand_timer_placeholders(
    text: str, timers: list[StepTimer] | None, *, markup: bool = False
) -> str:
    """Expand timers by position, preserving unresolved text and punctuation.

    With ``markup=True`` (Markdown/HTML-rendered targets) an unresolved
    placeholder is emitted as a code span so renderers keep it visible.
    """
    if not text:
        return text
    timers = timers or []
    unresolved = UNRESOLVED_TIMER_MARKUP if markup else TIMER_PLACEHOLDER
    if not timers and not markup:
        return text
    timer_index = [0]

    def replacer(match: re.Match[str]) -> str:
        idx = timer_index[0]
        timer_index[0] += 1
        punctuation = match.group("punct") or ""
        timer_text = format_timer(timers[idx]) if idx < len(timers) else ""
        if not timer_text:
            return f"{unresolved}{punctuation}"
        if punctuation == "." and timer_text.endswith("."):
            timer_text = timer_text[:-1]
        return f"{timer_text}{punctuation}"

    return TIMER_PLACEHOLDER_PATTERN.sub(replacer, text)


def get_step_text(
    step: RecipeStep, language: str = "de", *, markup: bool = False
) -> str:
    """Get localized step text with timer placeholders expanded."""
    raw = localized_fallback(step.title, language) or ""
    return expand_timer_placeholders(raw, step.timers or [], markup=markup)


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
    placeholder with no remaining or no usable timer becomes a visible Markdown
    code span. The original ``step.timers`` list is not mutated.
    """
    timer_iter = iter(getattr(step, "timers", None) or [])

    def repl(match: re.Match[str]) -> str:
        timer = next(timer_iter, None)
        if timer is None:
            return UNRESOLVED_TIMER_MARKUP
        if timer.min_or_exact is not None and timer.max is not None:
            return f"{timer.min_or_exact}–{timer.max}m"
        if timer.min_or_exact is not None:
            return f"{timer.min_or_exact}m"
        if timer.max is not None:
            return f"≤{timer.max}m"
        return UNRESOLVED_TIMER_MARKUP

    return re.sub(re.escape(TIMER_PLACEHOLDER), repl, text)


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


MAX_EXPORT_FILENAME_BYTES = 240


def bounded_export_filename(stem: str, extension: str, *, suffix: str = "") -> str:
    """Fit a display stem in UTF-8 bytes, preserving the full identity suffix."""
    budget = MAX_EXPORT_FILENAME_BYTES - len((suffix + extension).encode("utf-8"))
    if budget < 1:
        raise UserFacingError("Export filename suffix exceeds the filename byte budget")
    display = (
        (stem or "recipe").encode("utf-8")[:budget].decode("utf-8", errors="ignore")
    )
    # A very small budget can end in the middle of the first Unicode character.
    return f"{display or 'r'}{suffix}{extension}"


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
    Display stems are shortened to fit 240 UTF-8 bytes including each suffix
    and extension; identity/counter suffixes remain intact.
    """
    directory = Path(directory)
    base_suffix = f"-{disambiguator}" if disambiguator else ""
    suffixes = itertools.chain(
        [""],
        [base_suffix] if disambiguator else [],
        (f"{base_suffix}-{counter}" for counter in itertools.count(2)),
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
        for suffix in suffixes:
            target = directory / bounded_export_filename(stem, extension, suffix=suffix)
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
