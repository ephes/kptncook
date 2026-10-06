import itertools
import re
import shutil
import zipfile
from pathlib import Path
from collections.abc import Iterable

from unidecode import unidecode

from kptncook.models import Image, RecipeStep, StepTimer, localized_fallback

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


def get_step_text(step: RecipeStep) -> str:
    """Get localized step text with timer placeholders expanded."""
    raw = localized_fallback(step.title) or ""
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

    Timers are consumed in order using ``timer.min_or_exact`` (minutes). A
    placeholder with no remaining timer is left untouched. The original
    ``step.timers`` list is not mutated.
    """
    timer_iter = iter(getattr(step, "timers", None) or [])

    def repl(match: re.Match[str]) -> str:
        timer = next(timer_iter, None)
        if timer is None or timer.min_or_exact is None:
            return match.group(0)
        return f"{timer.min_or_exact}m"

    return re.sub(r"<timer>", repl, text)


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
    name is claimed with an exclusive create, so an existing file is never
    replaced, even if it appears between the check and the write.
    """
    directory = Path(directory)
    base = f"{stem}-{disambiguator}" if disambiguator else stem
    candidates = itertools.chain(
        [stem],
        [base] if disambiguator else [],
        (f"{base}-{counter}" for counter in itertools.count(2)),
    )
    for candidate in candidates:
        target = directory / f"{candidate}{extension}"
        with open(source, "rb") as src:
            try:
                dst = open(target, "xb")
            except FileExistsError:
                continue
            # from here on the target is ours, so it may be cleaned up on failure
            try:
                with dst:
                    shutil.copyfileobj(src, dst)
            except BaseException:
                target.unlink(missing_ok=True)
                raise
        Path(source).unlink()
        return target
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
