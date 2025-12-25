import re
import shutil
import zipfile
from pathlib import Path
from typing import Iterable

from unidecode import unidecode

from kptncook.models import Image

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
