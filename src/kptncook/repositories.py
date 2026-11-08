"""
Repositories to store recipes.

Atm only uses json. But this could also be a sqlite or a
remote api, maybe mealie... hmm.
"""

import errno
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import List  # noqa F401
from collections.abc import Iterator

import portalocker

from pydantic import BaseModel, RootModel, ValidationError


MAX_STORE_BYTES = 64 * 1024 * 1024


class RepositoryError(Exception):
    """Raised when the repository file cannot be read or written safely."""


def format_validation_error(exc: ValidationError) -> str:
    error = exc.errors(include_url=False)[0]
    location = ".".join(str(part) for part in error.get("loc", ()))
    message = error.get("msg", "Validation failed")
    if location:
        return f"{location}: {message}"
    return message


class RecipeInDb(BaseModel):
    date: date
    data: dict

    @property
    def id(self):
        return self.data["_id"]["$oid"]


class RecipeListInDb(RootModel):
    root: list[RecipeInDb]

    def __iter__(self):
        return iter(self.root)

    def __getitem__(self, item):
        return self.root[item]


class RecipeRepository:
    name: str = "kptncook.json"

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)

    @property
    def path(self) -> Path:
        return self.base_dir / self.name

    @property
    def backup_path(self) -> Path:
        return self.base_dir / f"{self.name}.backup"

    @property
    def lock_path(self) -> Path:
        return self.base_dir / f"{self.name}.lock"

    def _ensure_parent_dir(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def create_backup(self) -> None:
        if not self.path.exists():
            return
        temp_path: Path | None = None
        try:
            with self.path.open("rb") as source:
                with tempfile.NamedTemporaryFile(
                    "wb",
                    dir=self.path.parent,
                    prefix=f".{self.backup_path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as target:
                    temp_path = Path(target.name)
                    shutil.copyfileobj(source, target)
                    target.flush()
                    os.fsync(target.fileno())
            os.replace(temp_path, self.backup_path)
            self._fsync_directory()
        except OSError as exc:
            raise RepositoryError(
                f"Could not write repository backup {self.backup_path}: {exc}"
            ) from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _build_by_id(
        self, recipes: list[RecipeInDb] | RecipeListInDb, *, source: str = "Repository"
    ) -> dict[str, RecipeInDb]:
        by_id: dict[str, RecipeInDb] = {}
        positions: dict[str, int] = {}
        for position, recipe in enumerate(recipes, start=1):
            identifier = recipe.data.get("_id")
            oid = identifier.get("$oid") if isinstance(identifier, dict) else None
            if not isinstance(oid, str) or not oid.strip():
                raise RepositoryError(
                    f"{source} record {position} has an invalid identifier; "
                    "expected _id.$oid to be a nonempty string."
                )
            if oid in positions:
                action = (
                    "check the incoming batch before retrying."
                    if source == "Incoming"
                    else "repair duplicates before retrying."
                )
                raise RepositoryError(
                    f"{source} record {position} has a duplicate identifier "
                    f"already used by record {positions[oid]}; {action}"
                )
            positions[oid] = position
            by_id[oid] = recipe
        return by_id

    def _fsync_directory(self) -> None:
        try:
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory_fd)
        except OSError:
            pass
        finally:
            os.close(directory_fd)

    @contextmanager
    def _write_lock(self) -> Iterator[None]:
        self._ensure_parent_dir()
        with self.lock_path.open("a+", encoding="utf-8") as lock_file:
            try:
                portalocker.lock(lock_file, portalocker.LOCK_EX)
            except portalocker.exceptions.LockException as exc:
                raise RepositoryError(
                    "Could not lock the recipe repository; mutations require supported "
                    "OS advisory locking. Use a local filesystem with locking support."
                ) from exc
            try:
                yield
            finally:
                portalocker.unlock(lock_file)

    def _write_models(self, locked):
        self.create_backup()
        self._ensure_parent_dir()
        models = RecipeListInDb.model_validate(locked.values())
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as f:
                temp_path = Path(f.name)
                f.write(models.model_dump_json())
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, self.path)
            self._fsync_directory()
        except OSError as exc:
            raise RepositoryError(
                f"Could not write repository file {self.path}: {exc}"
            ) from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _fetch_all(self):
        """
        Fetch dict of pydantic models from json in self.path
        """
        try:
            if self.path.is_symlink():
                raise RepositoryError(
                    "Repository must be a regular file, not a symlink."
                )
            flags = (
                os.O_RDONLY
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_BINARY", 0)
            )
            fd = os.open(self.path, flags)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    raise RepositoryError("Repository must be a regular file.")
                if info.st_size > MAX_STORE_BYTES:
                    raise RepositoryError("Repository exceeds the 64 MiB size limit.")
                with os.fdopen(fd, "rb", closefd=False) as source:
                    raw_bytes = source.read(MAX_STORE_BYTES + 1)
                if len(raw_bytes) > MAX_STORE_BYTES:
                    raise RepositoryError("Repository exceeds the 64 MiB size limit.")
            finally:
                os.close(fd)
            raw_data = raw_bytes.decode("utf-8")
        except FileNotFoundError:
            return []
        except UnicodeDecodeError as exc:
            raise RepositoryError("Repository contains invalid UTF-8 data.") from exc
        except OSError as exc:
            category = errno.errorcode.get(exc.errno or 0, "IO_ERROR")
            raise RepositoryError(
                f"Could not read repository file ({category})."
            ) from exc
        try:
            return RecipeListInDb.model_validate_json(raw_data)
        except ValidationError as exc:
            raise RepositoryError(
                f"Repository file {self.path} contains invalid data: "
                f"{format_validation_error(exc)}"
            ) from exc

    def list_by_id(self):
        return self._build_by_id(self.list())

    def needs_to_be_synced(self, _date: date):
        """
        Return True if there are no recipes for date.
        """
        return not any(recipe.date == _date for recipe in self.list())

    def add(self, recipe: RecipeInDb):
        incoming = self._build_by_id([recipe], source="Incoming")
        with self._write_lock():
            locked = self._build_by_id(self._fetch_all())
            locked.update(incoming)
            self._write_models(locked)

    def add_list(self, recipes: list[RecipeInDb]):
        incoming = self._build_by_id(recipes, source="Incoming")
        with self._write_lock():
            locked = self._build_by_id(self._fetch_all())
            locked.update(incoming)
            self._write_models(locked)

    def delete_by_ids(self, ids: list[str]) -> tuple[list[str], list[str]]:
        with self._write_lock():
            locked = self._build_by_id(self._fetch_all())
            key_map = {str(key): key for key in locked.keys()}
            deleted: list[str] = []
            missing: list[str] = []
            for oid in dict.fromkeys(str(value) for value in ids):
                key = key_map.get(str(oid))
                if key is None:
                    missing.append(str(oid))
                    continue
                locked.pop(key)
                deleted.append(str(oid))
            if deleted:
                self._write_models(locked)
            return deleted, missing

    def list(self):
        return list(self._fetch_all())
