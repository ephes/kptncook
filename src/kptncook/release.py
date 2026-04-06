from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
RELEASE_HEADING_PATTERN = re.compile(
    r"^(?P<version>\d+\.\d+\.\d+) - (?P<date>\d{4}-\d{2}-\d{2})\n=+\n",
    re.MULTILINE,
)
UNRELEASED_PREFIX = "Unreleased\n==========\n"


class ReleaseError(Exception):
    """Raised when the release workflow inputs are invalid."""


@dataclass(frozen=True)
class ReleaseNotes:
    version: str
    release_date: str
    body: str

    @property
    def heading(self) -> str:
        return f"{self.version} - {self.release_date}"

    def render(self) -> str:
        return f"{self.heading}\n{'=' * len(self.heading)}\n\n{self.body}\n"


def _validate_version(version: str) -> None:
    if not VERSION_PATTERN.match(version):
        raise ReleaseError(
            f"Invalid version '{version}'. Expected MAJOR.MINOR.PATCH, for example 0.0.30."
        )


def _validate_release_date(release_date: str) -> str:
    try:
        return date.fromisoformat(release_date).isoformat()
    except ValueError as exc:
        raise ReleaseError(
            f"Invalid release date '{release_date}'. Expected YYYY-MM-DD."
        ) from exc


def _split_changelog(changelog_text: str) -> tuple[str, str]:
    if not changelog_text.startswith(UNRELEASED_PREFIX):
        raise ReleaseError("CHANGELOG.md must start with the Unreleased heading.")

    remainder = changelog_text[len(UNRELEASED_PREFIX) :]
    match = RELEASE_HEADING_PATTERN.search(remainder)
    if match is None:
        body = remainder.strip()
        trailing = ""
    else:
        body = remainder[: match.start()].strip()
        trailing = remainder[match.start() :].lstrip("\n")
    return body, trailing


def _read_release_notes(changelog_path: Path, version: str) -> ReleaseNotes:
    changelog_text = changelog_path.read_text()
    match = re.search(
        rf"^(?P<heading>{re.escape(version)} - (?P<date>\d{{4}}-\d{{2}}-\d{{2}}))\n=+\n",
        changelog_text,
        re.MULTILINE,
    )
    if match is None:
        raise ReleaseError(f"Could not find release {version} in {changelog_path}.")

    body_start = match.end()
    next_match = RELEASE_HEADING_PATTERN.search(changelog_text, body_start)
    body_end = next_match.start() if next_match is not None else len(changelog_text)
    body = changelog_text[body_start:body_end].strip()
    return ReleaseNotes(
        version=version,
        release_date=match.group("date"),
        body=body,
    )


def update_changelog_for_release(
    changelog_path: Path, *, version: str, release_date: str
) -> None:
    _validate_version(version)
    normalized_date = _validate_release_date(release_date)
    changelog_text = changelog_path.read_text()
    unreleased_body, trailing = _split_changelog(changelog_text)
    if not unreleased_body:
        raise ReleaseError("Unreleased changelog section is empty.")
    if re.search(
        rf"^{re.escape(version)} - \d{{4}}-\d{{2}}-\d{{2}}$",
        changelog_text,
        re.MULTILINE,
    ):
        raise ReleaseError(f"Release {version} already exists in {changelog_path}.")

    release_notes = ReleaseNotes(
        version=version,
        release_date=normalized_date,
        body=unreleased_body,
    )
    parts = [UNRELEASED_PREFIX, "\n", release_notes.render()]
    if trailing:
        parts.extend(["\n", trailing, "" if trailing.endswith("\n") else "\n"])
    changelog_path.write_text("".join(parts))


def update_pyproject_version(pyproject_path: Path, *, version: str) -> None:
    _validate_version(version)
    pyproject_text = pyproject_path.read_text()
    updated_text, replacements = re.subn(
        r'(?m)^version = "([^"]+)"$',
        f'version = "{version}"',
        pyproject_text,
        count=1,
    )
    if replacements != 1:
        raise ReleaseError(f"Could not update version in {pyproject_path}.")
    pyproject_path.write_text(updated_text)


def prepare_release(
    changelog_path: Path,
    pyproject_path: Path,
    *,
    version: str,
    release_date: str | None = None,
) -> str:
    normalized_date = _validate_release_date(release_date or date.today().isoformat())
    update_changelog_for_release(
        changelog_path,
        version=version,
        release_date=normalized_date,
    )
    update_pyproject_version(pyproject_path, version=version)
    return normalized_date


def render_release_notes(changelog_path: Path, *, version: str) -> str:
    notes = _read_release_notes(changelog_path, version)
    return notes.render()


def draft_github_release(changelog_path: Path, *, version: str) -> None:
    notes = render_release_notes(changelog_path, version=version)
    subprocess.run(
        [
            "gh",
            "release",
            "create",
            f"v{version}",
            "--draft",
            "--title",
            f"v{version}",
            "--notes",
            notes,
        ],
        check=True,
        text=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Release helpers for kptncook.")
    parser.add_argument(
        "--changelog",
        type=Path,
        default=Path("CHANGELOG.md"),
        help="Path to CHANGELOG.md.",
    )
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path("pyproject.toml"),
        help="Path to pyproject.toml.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser(
        "prepare",
        help="Move Unreleased changelog entries into a release section and bump pyproject version.",
    )
    prepare_parser.add_argument("version", help="Release version, for example 0.0.30.")
    prepare_parser.add_argument(
        "--date",
        dest="release_date",
        help="Release date in YYYY-MM-DD format. Defaults to today.",
    )

    notes_parser = subparsers.add_parser(
        "notes", help="Print the changelog notes for a given release version."
    )
    notes_parser.add_argument("version", help="Release version to extract.")

    draft_parser = subparsers.add_parser(
        "draft", help="Create a draft GitHub release from the changelog section."
    )
    draft_parser.add_argument("version", help="Release version to draft.")

    args = parser.parse_args(argv)

    try:
        if args.command == "prepare":
            release_date = prepare_release(
                args.changelog,
                args.pyproject,
                version=args.version,
                release_date=args.release_date,
            )
            print(f"Prepared release {args.version} dated {release_date}.")
            return 0
        if args.command == "notes":
            print(render_release_notes(args.changelog, version=args.version), end="")
            return 0
        draft_github_release(args.changelog, version=args.version)
        print(f"Created draft GitHub release v{args.version}.")
        return 0
    except ReleaseError as exc:
        parser.exit(1, f"error: {exc}\n")
    except subprocess.CalledProcessError as exc:
        parser.exit(exc.returncode, f"error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
