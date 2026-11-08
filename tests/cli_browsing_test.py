"""Registered CLI contracts with synthetic workflow results and no API calls."""

from datetime import date
from importlib import import_module

import pytest
from click import unstyle
from typer.testing import CliRunner

from kptncook.repositories import RecipeInDb
from kptncook.services.discovery import DiscoveryListSummary, DiscoveryScreenData
from kptncook.services.repository import InvalidStoredRecipe
from kptncook.services.workflows import (
    MarkdownExportResult,
    PaprikaExportResult,
    TandoorExportResult,
    UserFacingError,
)

cli = import_module("kptncook.cli")
# Keep Rich wrapping independent of the invoking terminal.
runner = CliRunner(env={"COLUMNS": "120"})


@pytest.mark.parametrize("hide_quick_search", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_discovery_screen_display(mocker, hide_quick_search, empty):
    workflow = mocker.patch.object(
        cli,
        "get_discovery_screen",
        return_value=DiscoveryScreenData(
            lists=[]
            if empty
            else [
                DiscoveryListSummary("list-soup", "Synthetic soups", "curated"),
                DiscoveryListSummary(None, None, None),
            ],
            quick_search=[] if empty else ["Synthetic quick soup"],
        ),
    )

    result = runner.invoke(
        cli.app,
        ["discovery-screen"] + (["--no-quick-search"] if hide_quick_search else []),
    )

    assert result.exit_code == 0, result.output
    workflow.assert_called_once_with()
    if empty:
        assert "No discovery lists found." in result.output
    else:
        assert "Discovery lists:" in result.output
        assert "- list-soup | Synthetic soups | curated" in result.output
        assert "- - | - | -" in result.output
    if hide_quick_search:
        assert "quick search" not in result.output.lower()
        assert "Synthetic quick soup" not in result.output
    elif empty:
        assert "No quick search entries found." in result.output
    else:
        assert "Quick search:" in result.output
        assert "- Synthetic quick soup" in result.output


@pytest.mark.parametrize(
    "ingredients, expected_lines",
    [
        (
            [
                {"_id": {"$oid": "ingredient-soup"}, "numberTitle": {"de": "Soup"}},
                {"id": "ingredient-salt"},
                {"name": "Synthetic herb"},
                {},
            ],
            [
                "Popular ingredients:",
                "- ingredient-soup | Soup",
                "- ingredient-salt | -",
                "- - | Synthetic herb",
            ],
        ),
        ([], ["No popular ingredients found."]),
        ([{}, {"irrelevant": "hidden"}], ["No popular ingredients found."]),
    ],
)
def test_popular_ingredients_display(mocker, ingredients, expected_lines):
    workflow = mocker.patch.object(
        cli, "list_popular_ingredients_workflow", return_value=ingredients
    )

    result = runner.invoke(cli.app, ["ingredients-popular"])

    assert result.exit_code == 0, result.output
    workflow.assert_called_once_with()
    assert result.output.strip().splitlines() == expected_lines


# Keep both selection commands on the same contract: real option parsing and
# normalization, with only workflow and persistence boundaries replaced.
SELECTION_COMMANDS = [
    pytest.param(
        (
            "recipes-with-ingredients",
            "--ingredient-id",
            "get_recipes_with_ingredients",
            "No recipes found.",
        ),
        id="ingredient-search",
    ),
    pytest.param(
        (
            "onboarding",
            "--tag",
            "get_onboarding_recipes",
            "No onboarding recipes found.",
        ),
        id="onboarding",
    ),
]


@pytest.fixture(params=SELECTION_COMMANDS)
def selection(request):
    return request.param


@pytest.fixture
def synthetic_recipes():
    return [
        RecipeInDb(
            date=date(2026, 1, 1),
            data={"_id": {"$oid": oid}, "localizedTitle": {"en": title}},
        )
        for oid, title in [
            ("000000000000000000000001", "Synthetic soup"),
            ("000000000000000000000002", "Synthetic salad"),
        ]
    ]


@pytest.mark.parametrize("save", [False, True])
@pytest.mark.parametrize("empty", [False, True])
def test_selection_normalizes_and_saves_only_when_requested(
    mocker, selection, synthetic_recipes, save, empty
):
    command, option, workflow_name, empty_message = selection
    recipes = [] if empty else synthetic_recipes
    workflow = mocker.patch.object(cli, workflow_name, return_value=recipes)
    save_workflow = mocker.patch.object(cli, "save_recipe_entries", return_value=2)

    result = runner.invoke(
        cli.app,
        [command, option, " first, second,first ", option, " second,third, "]
        + (["--save"] if save else []),
    )

    assert result.exit_code == 0, result.output
    workflow.assert_called_once_with(["first", "second", "third"])
    if empty:
        assert empty_message in result.output
        save_workflow.assert_not_called()
        assert "Added" not in result.output
    elif save:
        save_workflow.assert_called_once_with(recipes)
        assert "Added 2 recipes to local repository" in result.output
        assert "Synthetic soup" not in result.output
    else:
        save_workflow.assert_not_called()
        for title in ("Synthetic soup", "Synthetic salad"):
            assert title in result.output
        assert "Added" not in result.output


@pytest.mark.parametrize("blank", [False, True], ids=["missing-option", "blank-values"])
def test_selection_rejects_empty_input_before_workflows(mocker, selection, blank):
    command, option, workflow_name, _ = selection
    workflow = mocker.patch.object(cli, workflow_name)
    save_workflow = mocker.patch.object(cli, "save_recipe_entries")

    result = runner.invoke(
        cli.app, [command, "--save"] + ([option, " , ", option, "  "] if blank else [])
    )

    assert result.exit_code == (1 if blank else 2)
    output = unstyle(result.output)
    assert ("non-empty" if blank else "Missing option") in output
    assert option in output
    workflow.assert_not_called()
    save_workflow.assert_not_called()


@pytest.mark.parametrize("fail_save", [False, True], ids=["search-error", "save-error"])
def test_selection_workflow_errors(mocker, selection, synthetic_recipes, fail_save):
    command, option, workflow_name, _ = selection
    workflow = mocker.patch.object(cli, workflow_name, return_value=synthetic_recipes)
    save_workflow = mocker.patch.object(cli, "save_recipe_entries")
    failing_workflow = save_workflow if fail_save else workflow
    failing_workflow.side_effect = UserFacingError("Synthetic selection failure")

    result = runner.invoke(cli.app, [command, option, "first", "--save"])

    assert result.exit_code == 1
    assert "Synthetic selection failure" in result.output
    assert "Added" not in result.output
    workflow.assert_called_once_with(["first"])
    if fail_save:
        save_workflow.assert_called_once_with(synthetic_recipes)
    else:
        save_workflow.assert_not_called()


EXPORT_FORMATS = ["paprika", "tandoor", "markdown"]


@pytest.mark.parametrize("format_name", EXPORT_FORMATS)
@pytest.mark.parametrize("recipe_id", [None, "000000000000000000000001"])
def test_export_dispatch_and_output(mocker, format_name, recipe_id):
    warnings = [InvalidStoredRecipe(3, "synthetic-broken", "steps: Field required")]
    results = {
        "paprika": PaprikaExportResult("synthetic.paprikarecipes", warnings),
        "tandoor": TandoorExportResult(["soup.zip", "salad.zip"], warnings),
        "markdown": MarkdownExportResult(
            ["export_md/soup.md", "export_md/salad.md"], warnings
        ),
    }
    workflows = {
        name: mocker.patch.object(
            cli, f"export_recipes_to_{name}_workflow", return_value=results[name]
        )
        for name in EXPORT_FORMATS
    }

    result = runner.invoke(
        cli.app,
        [f"export-recipes-to-{format_name}"] + ([recipe_id] if recipe_id else []),
    )

    assert result.exit_code == 0, result.output
    for name, workflow in workflows.items():
        if name == "markdown" and name == format_name:
            workflow.assert_called_once_with(recipe_id, 2)
        elif name == format_name:
            workflow.assert_called_once_with(recipe_id)
        else:
            workflow.assert_not_called()
    assert "Warning:" in result.output
    assert "skipped 1 invalid stored recipe" in result.output
    assert "synthetic-broken: steps: Field required" in result.output
    if format_name == "paprika":
        assert "synthetic.paprikarecipes" in result.output
        assert "Paprika App" in result.output
    elif format_name == "tandoor":
        assert "soup.zip, salad.zip" in result.output
        assert "Tandoor" in result.output
    else:
        assert "2 recipe(s) were exported as Markdown" in result.output
        assert "export_md/soup.md" in result.output
        assert "export_md/salad.md" in result.output


@pytest.mark.parametrize("servings_args", [["--servings", "4"], ["-s", "4"]])
def test_markdown_export_passes_servings(mocker, servings_args):
    workflow = mocker.patch.object(
        cli,
        "export_recipes_to_markdown_workflow",
        return_value=MarkdownExportResult(["export_md/soup.md"], []),
    )

    result = runner.invoke(cli.app, ["export-recipes-to-markdown", *servings_args])

    assert result.exit_code == 0, result.output
    workflow.assert_called_once_with(None, 4)


def test_markdown_export_rejects_non_positive_servings(mocker):
    workflow = mocker.patch.object(cli, "export_recipes_to_markdown_workflow")

    result = runner.invoke(cli.app, ["export-recipes-to-markdown", "--servings", "0"])

    assert result.exit_code == 2
    workflow.assert_not_called()


@pytest.mark.parametrize(
    "command, workflow_name",
    [
        ("discovery-screen", "get_discovery_screen"),
        ("ingredients-popular", "list_popular_ingredients_workflow"),
        *[
            (f"export-recipes-to-{name}", f"export_recipes_to_{name}_workflow")
            for name in EXPORT_FORMATS
        ],
    ],
)
def test_browsing_and_export_errors_exit_with_message(mocker, command, workflow_name):
    workflow = mocker.patch.object(
        cli, workflow_name, side_effect=UserFacingError("Synthetic workflow failure")
    )

    result = runner.invoke(cli.app, [command])

    assert result.exit_code == 1
    assert "Synthetic workflow failure" in result.output
    assert "exported" not in result.output
    workflow.assert_called_once()
