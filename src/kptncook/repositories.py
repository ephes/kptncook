"""
Repositories to store recipes.

Atm only uses json. But this could also be a sqlite or a
remote api, maybe mealie... hmm.
"""

import os
import shutil
import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import List  # noqa F401
from collections.abc import Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - only used on platforms without fcntl
    fcntl = None  # type: ignore[assignment]

from pydantic import BaseModel, RootModel, ValidationError


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

    def create_backup(self):
        if self.path.exists():
            shutil.copyfile(self.path, self.backup_path)

    def _build_by_id(
        self, recipes: list[RecipeInDb] | RecipeListInDb
    ) -> dict[str, RecipeInDb]:
        return {recipe.id: recipe for recipe in recipes}

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
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

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
            if not self.path.exists():
                return []
            with self.path.open("r", encoding="utf-8") as f:
                raw_data = f.read()
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise RepositoryError(
                f"Could not read repository file {self.path}: {exc}"
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
        with self._write_lock():
            locked = self._build_by_id(self._fetch_all())
            locked[recipe.id] = recipe
            self._write_models(locked)

    def add_list(self, recipes: list[RecipeInDb]):
        with self._write_lock():
            locked = self._build_by_id(self._fetch_all())
            for recipe in recipes:
                locked[recipe.id] = recipe
            self._write_models(locked)

    def delete_by_ids(self, ids: list[str]) -> tuple[list[str], list[str]]:
        with self._write_lock():
            locked = self._build_by_id(self._fetch_all())
            key_map = {str(key): key for key in locked.keys()}
            deleted: list[str] = []
            missing: list[str] = []
            for oid in ids:
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
