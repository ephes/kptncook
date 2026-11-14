from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import portalocker

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
RELEASE_HEADING_PATTERN = re.compile(
    r"^(?P<version>\d+\.\d+\.\d+) - (?P<date>\d{4}-\d{2}-\d{2})\n=+\n",
    re.MULTILINE,
)
UNRELEASED_PREFIX = "Unreleased\n==========\n"
RELEASE_LOCK_TIMEOUT_SECONDS = 30.0


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


def _updated_changelog(
    changelog_text: str, changelog_path: Path, *, version: str, release_date: str
) -> str:
    _validate_version(version)
    normalized_date = _validate_release_date(release_date)
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
    return "".join(parts)


def _updated_pyproject(
    pyproject_text: str, pyproject_path: Path, *, version: str
) -> str:
    _validate_version(version)
    error = f"Could not update version in {pyproject_path}."
    try:
        parsed = tomllib.loads(pyproject_text)
        if not isinstance(parsed.get("project", {}).get("version"), str):
            raise ReleaseError(error)
        # Only ordinary, single-line assignments in an explicit project table
        # are edited. The parser verifies the complete result below, including
        # any apparent table/assignment text inside TOML multiline strings.
        in_project = False
        candidates = []
        offset = 0
        for line in pyproject_text.splitlines(keepends=True):
            if line.lstrip().startswith("["):
                in_project = bool(
                    re.fullmatch(
                        r"\s*\[\s*(?:project|\"project\"|'project')\s*\]\s*(?:#[^\r\n]*)?\r?\n?",
                        line,
                    )
                )
            if in_project:
                match = re.fullmatch(
                    r"[ \t]*(?:version|\"version\"|'version')[ \t]*=[ \t]*(?P<quote>[\"'])(?P<value>[^\"'\r\n]*)(?P=quote)[ \t]*(?:#[^\r\n]*)?\r?\n?",
                    line,
                )
                if match:
                    candidates.append(
                        (offset + match.start("value"), offset + match.end("value"))
                    )
            offset += len(line)
        if len(candidates) != 1:
            raise ReleaseError(error)
        start, end = candidates[0]
        updated = pyproject_text[:start] + version + pyproject_text[end:]
        parsed["project"]["version"] = version
        if tomllib.loads(updated) != parsed:
            raise ReleaseError(error)
        return updated
    except (ValueError, TypeError, AttributeError) as exc:
        raise ReleaseError(error) from exc


@dataclass(frozen=True)
class _Generation:
    content: bytes
    device: int
    inode: int
    modified: int


def _generation(path: Path) -> _Generation:
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        content = stream.read()
        after = os.fstat(stream.fileno())
    named = path.stat()

    def identity(info: os.stat_result) -> tuple[int, int, int, int]:
        return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size

    if identity(before) != identity(after) or identity(after) != identity(named):
        raise ReleaseError("Release source changed while reading; retry preparation.")
    return _Generation(content, after.st_dev, after.st_ino, after.st_mtime_ns)


def _require_generation(path: Path, expected: _Generation) -> None:
    if _generation(path) != expected:
        raise ReleaseError("Release source changed; retry preparation.")


def _atomic_replace(
    path: Path, content: bytes, *, expected: _Generation | None = None
) -> _Generation:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(path.stat().st_mode & 0o777)
        published = _generation(temporary)
        if expected is not None:
            _require_generation(path, expected)
        os.replace(temporary, path)
        return published
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _restore_changelog(path: Path, original: _Generation, owned: _Generation) -> None:
    try:
        _atomic_replace(path, original.content, expected=owned)
    except (OSError, ReleaseError) as exc:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=path.name + ".recovery-", delete=False
        ) as stream:
            stream.write(original.content)
            stream.flush()
            os.fsync(stream.fileno())
            recovery = stream.name
        raise ReleaseError(
            f"Could not restore changelog (changed or unavailable); recovery copy: {recovery}"
        ) from exc


def update_changelog_for_release(
    changelog_path: Path, *, version: str, release_date: str
) -> None:
    text = _updated_changelog(
        changelog_path.read_text(),
        changelog_path,
        version=version,
        release_date=release_date,
    )
    _atomic_replace(changelog_path, text.encode("utf-8"))


def update_pyproject_version(pyproject_path: Path, *, version: str) -> None:
    text = _updated_pyproject(
        pyproject_path.read_text(), pyproject_path, version=version
    )
    _atomic_replace(pyproject_path, text.encode("utf-8"))


def prepare_release(
    changelog_path: Path,
    pyproject_path: Path,
    *,
    version: str,
    release_date: str | None = None,
) -> str:
    normalized_date = _validate_release_date(release_date or date.today().isoformat())
    # Keep this inode: unlinking a lock lets later waiters lock a different file.
    lock_path = changelog_path.resolve().parent / ".kptncook-release.lock"

    def open_lock(path: str, flags: int) -> int:
        if lock_path.is_symlink():
            raise OSError("Unsafe release lock")
        fd = os.open(
            path,
            flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            0o600,
        )
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise OSError("Unsafe release lock")
            return fd
        except BaseException:
            os.close(fd)
            raise

    try:
        with portalocker.Lock(
            lock_path,
            mode="a",
            timeout=RELEASE_LOCK_TIMEOUT_SECONDS,
            check_interval=0.1,
            opener=open_lock,
            flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
        ) as handle:
            named, opened = lock_path.lstat(), os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(named.st_mode)
                or named.st_nlink != 1
                or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
            ):
                raise ReleaseError("Release lock changed; retry preparation.")
            original_changelog = _generation(changelog_path)
            original_project = _generation(pyproject_path)
            changelog = _updated_changelog(
                original_changelog.content.decode("utf-8")
                .replace("\r\n", "\n")
                .replace("\r", "\n"),
                changelog_path,
                version=version,
                release_date=normalized_date,
            )
            project = _updated_pyproject(
                original_project.content.decode("utf-8"),
                pyproject_path,
                version=version,
            )
            # Preflight both sources before publishing the first file.
            _require_generation(changelog_path, original_changelog)
            _require_generation(pyproject_path, original_project)
            owned = _atomic_replace(
                changelog_path, changelog.encode("utf-8"), expected=original_changelog
            )
            try:
                _require_generation(changelog_path, owned)
                _atomic_replace(
                    pyproject_path, project.encode("utf-8"), expected=original_project
                )
            except (OSError, ReleaseError):
                _restore_changelog(changelog_path, original_changelog, owned)
                raise
    except portalocker.exceptions.LockException as exc:
        raise ReleaseError(
            "Release preparation is busy or the lock is unavailable."
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise ReleaseError(f"Could not prepare release: {exc}") from exc
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
