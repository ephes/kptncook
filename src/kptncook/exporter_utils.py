import errno
import io
import itertools
import secrets
from decimal import Decimal
from html import escape
import logging
import os
import tempfile
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any
from collections.abc import Iterable

from unidecode import unidecode
from pathvalidate import validate_filename, ValidationError as FilenameValidationError

from kptncook.durability import fsync_directory
from kptncook.http_errors import UserFacingError
from kptncook.models import (
    Image,
    IngredientDetails,
    StepIngredientDetails,
    RecipeStep,
    StepTimer,
    localized_fallback,
)
from kptncook.recipe_admission import MAX_RECIPES

logger = logging.getLogger(__name__)

TIMER_PLACEHOLDER = "<timer>"
TIMER_PLACEHOLDER_PATTERN = re.compile(
    rf"{re.escape(TIMER_PLACEHOLDER)}(?P<punct>[.!?])?"
)
TIMER_MARKUP_PATTERN = re.compile(
    rf"{re.escape(escape(TIMER_PLACEHOLDER))}(?P<punct>[.!?])?"
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
    Source HTML metacharacters are escaped before trusted timer insertion.
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

    if markup:
        return TIMER_MARKUP_PATTERN.sub(replacer, escape(text, quote=False))
    return TIMER_PLACEHOLDER_PATTERN.sub(replacer, text)


def get_step_text(
    step: RecipeStep, language: str = "de", *, markup: bool = False
) -> str:
    """Get localized step text with timer placeholders expanded."""
    raw = localized_fallback(step.title, language) or ""
    return expand_timer_placeholders(raw, step.timers or [], markup=markup)


def format_quantity(quantity: float) -> str:
    """Keep every admitted decimal place without binary noise or exponents."""
    text = format(Decimal(str(quantity)), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def ingredient_name(
    details: IngredientDetails | StepIngredientDetails | None, language: str = "de"
) -> str | None:
    """Resolve nonempty food titles with the same precedence in every exporter."""
    if details is not None:
        for candidate in (
            details.uncountable_title,
            details.localized_title,
            details.number_title,
        ):
            title = localized_fallback(candidate, language=language)
            if title:
                return title
    return None


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

    return re.sub(re.escape(escape(TIMER_PLACEHOLDER)), repl, escape(text, quote=False))


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


class ExportPublicationUncertain(UserFacingError):
    """A complete final file is visible, but its directory sync failed."""

    def __init__(self, target: Path):
        self.target = target
        super().__init__(
            f"Export published as {target}; durability is uncertain. "
            "Inspect the visible files before retrying; no published files were removed."
        )


MAX_BATCH_ASSET_BYTES = 100 * 1024 * 1024
MAX_BATCH_OUTPUT_BYTES = 256 * 1024 * 1024


class ExportLimitError(UserFacingError):
    """A batch exhausted its cumulative byte allowance."""


class ExportBudget:
    """Count downloaded assets and output work before retaining/writing bytes."""

    def __init__(self) -> None:
        self.asset_bytes = 0
        self.output_bytes = 0

    def charge_asset(self, amount: int) -> None:
        if self.asset_bytes + amount > MAX_BATCH_ASSET_BYTES:
            raise ExportLimitError(
                "Batch asset byte allowance exceeded. Export a smaller selection."
            )
        self.asset_bytes += amount

    def check_asset_capacity(self) -> None:
        if self.asset_bytes >= MAX_BATCH_ASSET_BYTES:
            self.charge_asset(1)

    def charge_output(self, amount: int) -> None:
        if self.output_bytes + amount > MAX_BATCH_OUTPUT_BYTES:
            raise ExportLimitError(
                "Batch output byte allowance exceeded. Export a smaller selection."
            )
        self.output_bytes += amount


class BudgetedWriter(io.BufferedIOBase):
    """Meter writes to an already-owned file; the caller closes that file."""

    def __init__(self, raw: Any, budget: ExportBudget):
        self.raw = raw
        self.budget = budget

    def write(self, data: Any) -> int:
        self.budget.charge_output(len(data))
        return self.raw.write(data)

    def tell(self) -> int:
        return self.raw.tell()

    def seek(self, offset: int, whence: int = 0) -> int:
        return self.raw.seek(offset, whence)

    def seekable(self) -> bool:
        return self.raw.seekable()

    def writable(self) -> bool:
        return True

    def flush(self) -> None:
        if not self.raw.closed:
            self.raw.flush()


def validate_export_batch_size(count: int) -> None:
    if count > MAX_RECIPES:
        raise UserFacingError(f"Export batch exceeds {MAX_RECIPES} recipes")


class BatchExportError(UserFacingError):
    """An aborted batch, with receipts separated by publication state.

    Paths are bounded by the admitted batch (at most 4,096 recipes). Successful
    exporter return types remain unchanged. Never infer completion from a file
    merely being visible: post-link directory sync can still have failed.
    """

    def __init__(
        self,
        completed_paths: Iterable[Path],
        failed_recipe_id: str,
        remaining_count: int,
        cause: Exception,
        *,
        completed_assets: Iterable[Path] = (),
        retained_assets: Iterable[Path] = (),
        unpublished_count: int | None = None,
    ):
        self.completed_paths = tuple(completed_paths)
        self.completed_assets = tuple(completed_assets)
        self.failed_recipe_id = failed_recipe_id
        self.remaining_count = remaining_count
        self.unpublished_count = (
            remaining_count + 1 if unpublished_count is None else unpublished_count
        )
        self.retained_assets = tuple(retained_assets)
        self.uncertain_paths = (
            (cause.target,) if isinstance(cause, ExportPublicationUncertain) else ()
        )
        reason = (
            str(cause)
            if isinstance(cause, UserFacingError)
            and not isinstance(cause, ExportPublicationUncertain)
            else type(cause).__name__
        )
        lines = [f"Export stopped at recipe {failed_recipe_id}: {reason}."]
        for label, paths in (
            ("Completed export", self.completed_paths),
            ("Completed asset", self.completed_assets),
            ("Retained current asset", self.retained_assets),
            ("Export published; durability is uncertain", self.uncertain_paths),
        ):
            lines.extend(f"{label}: {path}" for path in paths)
        lines.append(f"Unpublished recipes: {self.unpublished_count}.")
        lines.append(
            f"Untouched recipes: {remaining_count}. Inspect receipts before retrying."
        )
        super().__init__("\n".join(lines))


MAX_EXPORT_FILENAME_BYTES = 240
MAX_SEQUENTIAL_EXPORT_ATTEMPTS = 16
MAX_RANDOM_EXPORT_ATTEMPTS = 4


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
    *,
    budget: ExportBudget | None = None,
) -> Path:
    """Move ``source`` into ``directory`` without overwriting an existing file.

    The first candidate is ``<stem><extension>``. If that name is taken, the
    ``disambiguator`` (for example a recipe id) is appended, then a counter:
    ``<stem>-<disambiguator><extension>``, ``<stem>-<disambiguator>-2<extension>``
    for at most 16 sequential attempts, then four random 16-hex suffixes.
    Exhaustion refuses publication with an actionable error. The target
    name is published by linking a flushed sibling staging file. Hard-link
    support is required: publication fails safely when unavailable. Existing
    files are never replaced and final names always contain complete data.
    Display stems are shortened to fit 240 UTF-8 bytes including each suffix
    and extension; identity/counter suffixes remain intact.
    """
    directory = Path(directory)
    base_suffix = f"-{disambiguator}" if disambiguator else ""
    sequential = itertools.islice(
        itertools.chain(
            [""],
            [base_suffix] if disambiguator else [],
            (f"{base_suffix}-{counter}" for counter in itertools.count(2)),
        ),
        MAX_SEQUENTIAL_EXPORT_ATTEMPTS,
    )
    suffixes = itertools.chain(
        sequential,
        (
            f"{base_suffix}-{secrets.token_hex(8)}"
            for _ in range(MAX_RANDOM_EXPORT_ATTEMPTS)
        ),
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
                shutil.copyfileobj(
                    src, BudgetedWriter(staged, budget) if budget else staged
                )
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
            try:
                fsync_directory(directory)
            except BaseException as exc:
                raise ExportPublicationUncertain(target) from exc
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
    raise UserFacingError(
        f"Export filename collision limit reached in {directory}. "
        "Move existing exports or choose a different export location before retrying."
    )


def write_zip(
    zip_path: Path,
    entries: Iterable[tuple[str, ZipContent]],
    *,
    budget: ExportBudget | None = None,
) -> None:
    with zip_path.open("wb") as output:
        with zipfile.ZipFile(
            BudgetedWriter(output, budget) if budget else output,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            allowZip64=True,
        ) as zip_file:
            for arcname, content in entries:
                if isinstance(content, Path):
                    if budget is None:
                        zip_file.write(content, arcname=arcname)
                        continue
                    member = zipfile.ZipInfo.from_file(content, arcname)
                    if member.is_dir():
                        zip_file.writestr(member, b"")
                        continue
                    member.compress_type = zip_file.compression
                    with (
                        content.open("rb") as source,
                        zip_file.open(member, "w") as dest,
                    ):
                        while chunk := source.read(64 * 1024):
                            budget.charge_output(len(chunk))
                            dest.write(chunk)
                else:
                    if budget is not None:
                        size = len(
                            content.encode("utf-8")
                            if isinstance(content, str)
                            else content
                        )
                        budget.charge_output(size)
                    zip_file.writestr(arcname, content)
