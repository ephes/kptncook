"""
All the domain models for kptncook live here.
"""

import logging
import math
from typing import Any
from urllib.parse import unquote_plus, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

logger = logging.getLogger(__name__)


def to_camel(string: str) -> str:
    """Convert snake_case to camelCase."""
    words = []
    for num, word in enumerate(string.split("_")):
        if num > 0:
            word = word.capitalize()
        words.append(word)
    return "".join(words)


def normalize_language(language: str) -> str:
    language = language.strip().lower()
    return language if language in {"de", "en", "es", "fr", "pt"} else "en"


class LocalizedString(BaseModel):
    en: str | None = None
    de: str | None = None
    es: str | None = None
    fr: str | None = None
    pt: str | None = None

    def fallback(self, language: str = "de") -> str | None:
        language = normalize_language(language)
        for lang in dict.fromkeys((language, "de", "en", "es", "fr", "pt")):
            candidate = getattr(self, lang)
            if candidate:
                return candidate
        return None

    @model_validator(mode="before")
    def coerce_string(cls, value: Any) -> dict[str, Any] | Any:
        if isinstance(value, str):
            # Default to German for string-only payloads.
            return {"de": value}
        if isinstance(value, dict):
            singular = value.get("singular")
            plural = value.get("plural")
            uncountable = value.get("uncountable")
            for candidate in (singular, plural, uncountable):
                if isinstance(candidate, dict):
                    # Prefer localized singular/plural if present.
                    return candidate
            for candidate in (uncountable, singular, plural):
                if isinstance(candidate, str):
                    # Fall back to the raw string if only singular/plural are present.
                    return {"de": candidate}
        return value


class LocalizedStepIngredientUnit(LocalizedString):
    """Localized unit labels for step ingredients."""


def localized_fallback(
    value: LocalizedString | None, language: str = "de"
) -> str | None:
    if value is None:
        return None
    return value.fallback(language)


class Nutrition(BaseModel):
    calories: int
    protein: int
    fat: int
    carbohydrate: int


class Image(BaseModel):
    name: str
    type: str | None = None
    url: str

    def get_image_with_api_key_url(self, api_key: str) -> "Image":
        """Attach the API key; raise ImageOriginError for a disallowed host."""
        from .image_origin import check_image_url

        check_image_url(self.url)
        parts = urlsplit(self.url)
        query = (
            [
                segment
                for segment in parts.query.split("&")
                if unquote_plus(segment.partition("=")[0]) != "kptnkey"
            ]
            if parts.query
            else []
        )
        query.append(urlencode({"kptnkey": api_key}))
        url_with_key = urlunsplit(parts._replace(query="&".join(query)))
        kwargs = self.model_dump() | {"url": url_with_key}
        return Image(**kwargs)


class RecipeId(BaseModel):
    oid: str = Field(..., alias="$oid")


class IngredientDetails(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)

    id: RecipeId | None = Field(None, alias="_id")
    typ: str
    localized_title: LocalizedString
    number_title: LocalizedString
    uncountable_title: LocalizedString | None = None
    category: str

    @model_validator(mode="before")
    def fix_json_errors(cls, values):
        if isinstance(values, dict):
            values = dict(values)
            if values.get("localizedTitle") is None:
                for key in ("uncountableTitle", "title", "numberTitle", "name"):
                    if values.get(key) is not None:
                        values["localizedTitle"] = values[key]
                        break
            if values.get("numberTitle") is None:
                for key in ("localizedTitle", "uncountableTitle", "title", "name"):
                    if values.get(key) is not None:
                        values["numberTitle"] = values[key]
                        break
            if values.get("uncountableTitle") is None and "numberTitle" in values:
                values["uncountableTitle"] = values["numberTitle"]
        return values


class StepIngredientUnit(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)

    name: str | None = None
    localized_title: LocalizedStepIngredientUnit | None = None
    short_title: LocalizedStepIngredientUnit | None = None

    @model_validator(mode="before")
    def coerce_unit_string(cls, value: Any) -> dict[str, Any] | Any:
        if isinstance(value, str):
            return {"name": value}
        return value


# KptnCook stores ingredient quantities for a single serving, including recipes
# with a ``fixedPortionCount`` (cookies, cakes, ...). Exporters scale them to the
# serving count they write: the fixed portion count when present (those recipes
# are balanced for one batch), otherwise this standard count.
DEFAULT_SERVINGS = 2


class QuantityScaleError(ValueError):
    """A quantity cannot be represented for the requested serving count."""


def scale_quantity(quantity: float | None, servings: int) -> float | None:
    """Scale a single-serving quantity, dropping binary float noise."""
    if quantity is None:
        return None
    if quantity == 0:
        return 0.0
    try:
        scaled = quantity * servings
    except OverflowError:
        raise QuantityScaleError("Scaled quantity must be finite") from None
    if not math.isfinite(scaled):
        raise QuantityScaleError("Scaled quantity must be finite")
    return round(scaled, 6)


class Ingredient(BaseModel):
    quantity: FiniteFloat | None = None
    measure: str | None = None
    ingredient: IngredientDetails


class StepIngredientDetails(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)

    id: RecipeId | None = Field(None, alias="_id")
    typ: str | None = None
    localized_title: LocalizedString | None = None
    number_title: LocalizedString | None = None
    uncountable_title: LocalizedString | None = None
    category: str | None = None

    @model_validator(mode="before")
    def coerce_titles(cls, values):
        if isinstance(values, str):
            return {"localizedTitle": values}
        if isinstance(values, dict):
            values = dict(values)
            if values.get("localizedTitle") is None:
                for key in ("uncountableTitle", "title", "name", "numberTitle"):
                    if values.get(key) is not None:
                        values["localizedTitle"] = values[key]
                        break
            if (
                values.get("uncountableTitle") is None
                and values.get("numberTitle") is not None
            ):
                values["uncountableTitle"] = values["numberTitle"]
        return values


class StepIngredient(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)

    quantity: FiniteFloat | None = None
    unit: StepIngredientUnit | None = None
    ingredient: StepIngredientDetails | None = None
    ingredient_id: str | None = Field(None, alias="ingredientId")

    @model_validator(mode="before")
    def normalize_fields(cls, values):
        if isinstance(values, dict):
            values = dict(values)
            if values.get("quantity") is None and values.get("amount") is not None:
                values["quantity"] = values["amount"]
            if values.get("unit") is None and values.get("measure") is not None:
                values["unit"] = values["measure"]
            if values.get("ingredientId") is None and isinstance(
                values.get("ingredient"), dict
            ):
                ingredient = values["ingredient"]
                ingredient_id = None
                if isinstance(ingredient.get("_id"), dict):
                    ingredient_id = ingredient["_id"].get("$oid")
                elif isinstance(ingredient.get("id"), dict):
                    ingredient_id = ingredient["id"].get("$oid")
                if ingredient_id is not None:
                    values["ingredientId"] = ingredient_id
        return values


class StepTimer(BaseModel):
    """Timer for a recipe step. Times are in minutes."""

    model_config = ConfigDict(
        alias_generator=to_camel, extra="ignore", populate_by_name=True
    )

    min_or_exact: int | None = Field(None, strict=True, ge=0)
    max: int | None = Field(None, strict=True, ge=0)

    @model_validator(mode="after")
    def ordered_range(self) -> "StepTimer":
        if (
            self.min_or_exact is not None
            and self.max is not None
            and self.max < self.min_or_exact
        ):
            raise ValueError("Timer maximum must be at least its minimum")
        return self


class RecipeStep(BaseModel):
    title: LocalizedString
    image: Image
    ingredients: list[StepIngredient | None] | None = None
    timers: list[StepTimer] | None = None


class Recipe(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)

    id: RecipeId = Field(..., alias="_id")
    uid: str | None = None
    rtype: str | None = None
    localized_title: LocalizedString
    author_comment: LocalizedString | None = None
    # Whole-recipe durations are integer minutes, capped at one year.
    preparation_time: int = Field(..., strict=True, ge=0, le=525600)
    cooking_time: int | None = Field(None, strict=True, ge=0, le=525600)
    recipe_nutrition: Nutrition
    active_tags: list[str] | None = None
    steps: list[RecipeStep] = Field(..., alias="steps")
    image_list: list[Image]
    ingredients: list[Ingredient]
    fixed_portion_count: int | None = Field(None, strict=True, gt=0)

    @property
    def servings(self) -> int:
        """Portions every exporter writes and scales ingredient quantities to."""
        return self.servings_or(DEFAULT_SERVINGS)

    def servings_or(self, default: int) -> int:
        """The fixed portion count of a batch recipe, otherwise ``default``."""
        return (
            self.fixed_portion_count
            if self.fixed_portion_count is not None
            else default
        )

    def validate_scaled_quantities(self, servings: int | None = None) -> None:
        """Preflight all quantity fields before export I/O, including overrides."""
        count = self.servings if servings is None else servings
        for position, ingredient in enumerate(self.ingredients):
            try:
                scale_quantity(ingredient.quantity, count)
            except QuantityScaleError as exc:
                raise QuantityScaleError(
                    f"ingredients.{position}.quantity: {exc}"
                ) from None
        for step_position, step in enumerate(self.steps):
            for position, step_ingredient in enumerate(step.ingredients or []):
                if step_ingredient is None:
                    continue
                try:
                    scale_quantity(step_ingredient.quantity, count)
                except QuantityScaleError as exc:
                    raise QuantityScaleError(
                        f"steps.{step_position}.ingredients.{position}.quantity: {exc}"
                    ) from None

    @model_validator(mode="after")
    def finite_scaled_quantities(self) -> "Recipe":
        self.validate_scaled_quantities()
        return self

    @model_validator(mode="before")
    def normalize_titles(cls, values):
        if isinstance(values, dict):
            values = dict(values)
            if values.get("localizedTitle") is None:
                title = values.get("title") or values.get("name")
                if title is not None:
                    values["localizedTitle"] = title
        return values

    def get_image_url(self, api_key: str) -> str | None:
        from .image_origin import ImageOriginError

        try:
            [image] = [i for i in self.image_list if i.type == "cover"]
        except ValueError:
            return None
        try:
            return image.get_image_with_api_key_url(api_key).url
        except ImageOriginError:
            logger.warning("Skipping cover image: not on an allowed image host")
            return None
