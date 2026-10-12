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


@pytest.mark.parametrize("version_line", ["", "version = '0.0.29'\n"])
def test_prepare_invalid_version_preserves_files(tmp_path, version_line):
    changelog = tmp_path / "CHANGELOG.md"
    project = tmp_path / "pyproject.toml"
    changelog.write_bytes(b"Unreleased\n==========\n\n- Keep notes.\r\n")
    project.write_text("[project]\n" + version_line)
    originals = changelog.read_bytes(), project.read_bytes()
    with pytest.raises(ReleaseError, match="Could not update version"):
        prepare_release(changelog, project, version="0.0.30")
    assert (changelog.read_bytes(), project.read_bytes()) == originals


def test_prepare_second_replace_failure_preserves_files(tmp_path, monkeypatch):
    import os

    changelog = tmp_path / "CHANGELOG.md"
    project = tmp_path / "pyproject.toml"
    changelog.write_bytes(b"Unreleased\n==========\n\n- Keep notes.\r\n")
    project.write_bytes(b'[project]\nversion = "0.0.29"\r\n')
    originals = changelog.read_bytes(), project.read_bytes()
    replace = os.replace
    failed = []

    def fail_project(source, target):
        if target == project:
            failed.append(True)
            raise OSError("injected second write failure")
        return replace(source, target)

    monkeypatch.setattr(os, "replace", fail_project)
    with pytest.raises(ReleaseError, match="injected second write failure"):
        prepare_release(changelog, project, version="0.0.30")
    assert failed == [True]
    assert (changelog.read_bytes(), project.read_bytes()) == originals
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "CHANGELOG.md",
        "pyproject.toml",
    ]


def test_prepare_accepts_crlf_changelog(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    project = tmp_path / "pyproject.toml"
    changelog.write_bytes(
        b"Unreleased\r\n==========\r\n\r\n- Keep notes.\r\n\r\n0.0.29 - 2026-02-11\r\n===================\r\n\r\n- Previous.\r\n"
    )
    project.write_bytes(b'[project]\r\nversion = "0.0.29"\r\n')
    prepare_release(changelog, project, version="0.0.30", release_date="2026-10-12")
    text = changelog.read_text()
    assert "0.0.30 - 2026-10-12" in text
    assert "0.0.29 - 2026-02-11" in text
    assert "- Keep notes." in text
    assert 'version = "0.0.30"' in project.read_text()
