import re
import shutil
import zipfile
from pathlib import Path
from collections.abc import Iterable

from unidecode import unidecode

from kptncook.models import Image, RecipeStep, StepTimer, localized_fallback

TIMER_PLACEHOLDER = "<timer>"


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

    def replacer(_: re.Match) -> str:
        idx = timer_index[0]
        timer_index[0] += 1
        if idx < len(timers):
            return format_timer(timers[idx])
        return ""

    return re.sub(re.escape(TIMER_PLACEHOLDER), replacer, text)


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


def move_to_target_dir(source: str | Path, target: str | Path) -> str:
    return shutil.move(str(source), str(target))


def write_zip(zip_path: Path, entries: Iterable[tuple[str, ZipContent]]) -> None:
    with zipfile.ZipFile(
        zip_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
    ) as zip_file:
        for arcname, content in entries:
            if isinstance(content, Path):
                zip_file.write(content, arcname=arcname)
            else:
                zip_file.writestr(arcname, content)
