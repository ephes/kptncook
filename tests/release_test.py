from __future__ import annotations

import subprocess

import pytest

from kptncook.release import (
    ReleaseError,
    draft_github_release,
    prepare_release,
    render_release_notes,
)


def test_prepare_release_moves_unreleased_entries_and_bumps_version(tmp_path):
    changelog_path = tmp_path / "CHANGELOG.md"
    changelog_path.write_text(
        "Unreleased\n"
        "==========\n"
        "\n"
        "### Fixes\n"
        "- Ship the thing.\n"
        "\n"
        "0.0.29 - 2026-02-11\n"
        "===================\n"
        "\n"
        "### Fixes\n"
        "- Previous release.\n"
    )
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text('[project]\nname = "kptncook"\nversion = "0.0.29"\n')

    release_date = prepare_release(
        changelog_path,
        pyproject_path,
        version="0.0.30",
        release_date="2026-04-06",
    )

    assert release_date == "2026-04-06"
    assert (
        pyproject_path.read_text()
        == '[project]\nname = "kptncook"\nversion = "0.0.30"\n'
    )
    assert changelog_path.read_text() == (
        "Unreleased\n"
        "==========\n"
        "\n"
        "0.0.30 - 2026-04-06\n"
        "===================\n"
        "\n"
        "### Fixes\n"
        "- Ship the thing.\n"
        "\n"
        "0.0.29 - 2026-02-11\n"
        "===================\n"
        "\n"
        "### Fixes\n"
        "- Previous release.\n"
    )


def test_prepare_release_rejects_empty_unreleased_section(tmp_path):
    changelog_path = tmp_path / "CHANGELOG.md"
    changelog_path.write_text("Unreleased\n==========\n\n")
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text('[project]\nversion = "0.0.29"\n')

    with pytest.raises(ReleaseError, match="Unreleased changelog section is empty"):
        prepare_release(
            changelog_path,
            pyproject_path,
            version="0.0.30",
            release_date="2026-04-06",
        )


def test_render_release_notes_returns_requested_section(tmp_path):
    changelog_path = tmp_path / "CHANGELOG.md"
    changelog_path.write_text(
        "Unreleased\n"
        "==========\n"
        "\n"
        "0.0.30 - 2026-04-06\n"
        "===================\n"
        "\n"
        "### Fixes\n"
        "- Ship the thing.\n"
        "\n"
        "0.0.29 - 2026-02-11\n"
        "===================\n"
        "\n"
        "### Fixes\n"
        "- Previous release.\n"
    )

    assert render_release_notes(changelog_path, version="0.0.30") == (
        "0.0.30 - 2026-04-06\n===================\n\n### Fixes\n- Ship the thing.\n"
    )


def test_draft_github_release_uses_changelog_notes(tmp_path, mocker):
    changelog_path = tmp_path / "CHANGELOG.md"
    changelog_path.write_text(
        "Unreleased\n"
        "==========\n"
        "\n"
        "0.0.30 - 2026-04-06\n"
        "===================\n"
        "\n"
        "### Fixes\n"
        "- Ship the thing.\n"
    )
    mock_run = mocker.patch("subprocess.run")

    draft_github_release(changelog_path, version="0.0.30")

    mock_run.assert_called_once_with(
        [
            "gh",
            "release",
            "create",
            "v0.0.30",
            "--draft",
            "--title",
            "v0.0.30",
            "--notes",
            "0.0.30 - 2026-04-06\n"
            "===================\n"
            "\n"
            "### Fixes\n"
            "- Ship the thing.\n",
        ],
        check=True,
        text=True,
    )


def test_main_exits_with_release_error_message(tmp_path, capsys):
    from kptncook.release import main

    changelog_path = tmp_path / "CHANGELOG.md"
    changelog_path.write_text("Unreleased\n==========\n\n")
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text('[project]\nversion = "0.0.29"\n')

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "--changelog",
                str(changelog_path),
                "--pyproject",
                str(pyproject_path),
                "prepare",
                "0.0.30",
                "--date",
                "2026-04-06",
            ]
        )

    assert exc_info.value.code == 1
    assert "Unreleased changelog section is empty" in capsys.readouterr().err


def test_main_propagates_gh_failure(tmp_path, mocker):
    from kptncook.release import main

    changelog_path = tmp_path / "CHANGELOG.md"
    changelog_path.write_text(
        "Unreleased\n"
        "==========\n"
        "\n"
        "0.0.30 - 2026-04-06\n"
        "===================\n"
        "\n"
        "### Fixes\n"
        "- Ship the thing.\n"
    )
    mocker.patch(
        "subprocess.run",
        side_effect=subprocess.CalledProcessError(2, ["gh", "release"]),
    )

    with pytest.raises(SystemExit) as exc_info:
        main(["--changelog", str(changelog_path), "draft", "0.0.30"])

    assert exc_info.value.code == 2
