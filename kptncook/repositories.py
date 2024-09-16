"""
Repositories to store recipes.

Atm only uses json. But this could also be a sqlite or a
remote api, maybe mealie... hmm.
"""

import shutil
from datetime import date
from pathlib import Path
from typing import List  # noqa F401

from pydantic import BaseModel, RootModel


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
        self.base_dir = base_dir

    @property
    def path(self) -> Path:
        return self.base_dir / self.name

    @property
    def backup_path(self) -> Path:
        return self.base_dir / f"{self.name}.backup"

    def create_backup(self):
        if self.path.exists():
            shutil.copyfile(self.path, self.backup_path)

    def _write_models(self, locked):
        self.create_backup()
        try:
            self.path.parent.mkdir(exist_ok=True)
        except AttributeError:
            # LocalPath in tests
            pass
        models = RecipeListInDb.model_validate(locked.values())
        with self.path.open("w", encoding="utf-8") as f:
            f.write(models.model_dump_json())

    def _fetch_all(self):
        """
        Fetch dict of pydantic models from json in self.path
        """
        try:
            if not self.path.exists():
                return []
            with self.path.open("r", encoding="utf-8") as f:
                recipes_in_db = RecipeListInDb.model_validate_json(f.read())
            return recipes_in_db
        except FileNotFoundError:
            return []

    def list_by_id(self):
        by_id = {}
        for recipe in self.list():
            by_id[recipe.id] = recipe
        return by_id

    def needs_to_be_synced(self, _date: date):
        """
        Return True if there are no recipes for date.
        """
        return not any(recipe.date == _date for recipe in self.list())

    def add(self, recipe: RecipeInDb):
        locked = self.list_by_id()
        locked[recipe.id] = recipe
        self._write_models(locked)

    def add_list(self, recipes: list[RecipeInDb]):
        locked = self.list_by_id()
        for recipe in recipes:
            locked[recipe.id] = recipe
        self._write_models(locked)

    def list(self):
        return list(self._fetch_all())
