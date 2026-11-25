from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path

import portalocker

from kptncook.durability import fsync_directory

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
NEWLINE_PATTERN = re.compile(r"\r\n|\r|\n")
RELEASE_LOCK_TIMEOUT_SECONDS = 30.0
MAX_RELEASE_INPUT_BYTES = 1024 * 1024
RELEASE_STAGE_PREFIX = ".kptncook-release-stage-"
RELEASE_INTENT_NAME = ".kptncook-release.intent"


class ReleaseError(Exception):
    """Raised when the release workflow inputs are invalid."""


class _ReleaseDurabilityError(ReleaseError):
    """A name change is visible, but its durability could not be confirmed."""


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
    changelog_text = _read_release_text(changelog_path)
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
    first_newline = NEWLINE_PATTERN.search(changelog_text)
    newline = first_newline.group() if first_newline else "\n"
    changelog_text = NEWLINE_PATTERN.sub("\n", changelog_text)
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
    return "".join(parts).replace("\n", newline)


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
    mode: int

    def fingerprint(self) -> str:
        digest = hashlib.sha256(self.content).hexdigest()
        return f"{self.device}:{self.inode}:{self.modified}:{self.mode}:{digest}"


def _generation(path: Path) -> _Generation:
    regular_file_error = f"Release source {path} must be a regular file."
    size_error = f"Release source {path} exceeds the 1 MiB size limit."
    changed_error = "Release source changed while reading; retry."

    def open_nonblocking(filename: str, flags: int) -> int:
        return os.open(filename, flags | getattr(os, "O_NONBLOCK", 0))

    def identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            info.st_dev,
            info.st_ino,
            info.st_mtime_ns,
            info.st_ctime_ns,
            info.st_size,
        )

    named = path.stat()
    if not stat.S_ISREG(named.st_mode):
        raise ReleaseError(regular_file_error)
    with open(path, "rb", opener=open_nonblocking) as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ReleaseError(regular_file_error)
        if before.st_size > MAX_RELEASE_INPUT_BYTES:
            raise ReleaseError(size_error)
        if identity(named) != identity(before):
            raise ReleaseError(changed_error)

        content = stream.read(MAX_RELEASE_INPUT_BYTES + 1)
        after = os.fstat(stream.fileno())
        if (
            len(content) > MAX_RELEASE_INPUT_BYTES
            or after.st_size > MAX_RELEASE_INPUT_BYTES
        ):
            raise ReleaseError(size_error)
        if (
            len(content) != after.st_size
            or identity(before) != identity(after)
            or identity(after) != identity(path.stat())
        ):
            raise ReleaseError(changed_error)
    return _Generation(
        content, after.st_dev, after.st_ino, after.st_mtime_ns, after.st_mode
    )


def _read_release_text(path: Path, *, normalize_newlines: bool = True) -> str:
    try:
        text = _generation(path).content.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReleaseError(f"Could not read release source {path} as UTF-8.") from exc
    return NEWLINE_PATTERN.sub("\n", text) if normalize_newlines else text


def _require_generation(path: Path, expected: _Generation) -> None:
    if _generation(path) != expected:
        raise ReleaseError("Release source changed; retry preparation.")


@contextmanager
def _stage_replacement(
    path: Path,
    content: bytes,
    *,
    retain_for: Path | None = None,
    private: bool = False,
) -> Iterator[tuple[Path, _Generation]]:
    if len(content) > MAX_RELEASE_INPUT_BYTES:
        raise ReleaseError(
            f"Prepared release file {path} exceeds the 1 MiB size limit."
        )
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=RELEASE_STAGE_PREFIX, delete=False
        ) as stream:
            temporary = Path(stream.name)
            if stream.write(content) != len(content):
                raise ReleaseError(f"Incomplete staged write for {path}.")
            stream.flush()
            if not private:
                temporary.chmod(path.stat().st_mode & 0o777)
            os.fsync(stream.fileno())
        yield temporary, _generation(temporary)
    finally:
        if temporary is not None and (retain_for is None or not retain_for.exists()):
            temporary.unlink(missing_ok=True)


def _sync_release_names(outcome: str, *paths: Path) -> None:
    failures: dict[Path, OSError] = {}
    for parent in dict.fromkeys(path.parent.resolve() for path in paths):
        try:
            fsync_directory(parent)
        except OSError as exc:
            failures[parent] = exc
    if failures:
        directories = ", ".join(map(str, failures))
        raise _ReleaseDurabilityError(
            f"{outcome} visible but durability is uncertain; could not sync {directories}."
        ) from next(iter(failures.values()))


def _atomic_replace(
    path: Path,
    content: bytes,
    *,
    expected: _Generation | None = None,
    register: Callable[[Path, _Generation], None] | None = None,
) -> None:
    with _stage_replacement(path, content) as (temporary, owned):
        uncertain: _ReleaseDurabilityError | None = None
        if register is not None:
            try:
                register(temporary, owned)
            except _ReleaseDurabilityError as exc:
                # The record naming this stage is visible; finish the
                # replacement and report the uncertain record afterwards.
                uncertain = exc
        if expected is not None:
            _require_generation(path, expected)
        try:
            os.replace(temporary, path)
        except OSError:
            if not _names_replacement(path, owned):
                raise
        except KeyboardInterrupt:
            if _names_replacement(path, owned):
                _sync_release_names(f"Replacement of {path} is", path)
            raise
        _sync_release_names(f"Replacement of {path} is", path)
        if uncertain is not None:
            raise uncertain


def _names_replacement(path: Path, staged: _Generation) -> bool:
    # A replace can complete before the call returns to Python. The staged
    # inode identifies publication even if an editor then writes to it.
    try:
        named = path.lstat()
    except FileNotFoundError:
        return False
    return (named.st_dev, named.st_ino) == (staged.device, staged.inode)


def _restore_changelog(
    path: Path,
    original: _Generation,
    owned: _Generation,
    register: Callable[[Path, _Generation], None],
) -> None:
    try:
        _require_generation(path, owned)
        _atomic_replace(path, original.content, expected=owned, register=register)
    except _ReleaseDurabilityError as exc:
        # Raised only after the rollback rename named the original content.
        raise _ReleaseDurabilityError(
            f"Changelog {path} was restored to its original content; {exc}"
        ) from exc
    except (OSError, ReleaseError) as exc:
        recovery: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=path.name + ".recovery-", delete=False
            ) as stream:
                recovery = Path(stream.name)
                if stream.write(original.content) != len(original.content):
                    raise ReleaseError(
                        "Incomplete recovery write; could not restore changelog."
                    )
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            if recovery is not None:
                recovery.unlink(missing_ok=True)
            raise
        _sync_release_names(f"Changelog recovery copy {recovery} is", path)
        raise ReleaseError(
            f"Could not restore changelog (changed or unavailable); recovery copy: {recovery}"
        ) from exc


def update_changelog_for_release(
    changelog_path: Path, *, version: str, release_date: str
) -> None:
    text = _updated_changelog(
        _read_release_text(changelog_path, normalize_newlines=False),
        changelog_path,
        version=version,
        release_date=release_date,
    )
    _atomic_replace(changelog_path, text.encode("utf-8"))


def update_pyproject_version(pyproject_path: Path, *, version: str) -> None:
    text = _updated_pyproject(
        _read_release_text(pyproject_path), pyproject_path, version=version
    )
    _atomic_replace(pyproject_path, text.encode("utf-8"))


@dataclass(frozen=True)
class _IntentFile:
    target: str
    temporary: str
    previous: str
    staged: str

    @classmethod
    def capture(
        cls, target: Path, temporary: Path, previous: _Generation, staged: _Generation
    ) -> _IntentFile:
        return cls(
            str(target.parent.resolve() / target.name),
            temporary.name,
            previous.fingerprint(),
            staged.fingerprint(),
        )

    @classmethod
    def load(cls, record: dict[str, str], path: Path) -> _IntentFile:
        entry = cls(**record)
        if (
            entry.target != str(path.parent.resolve() / path.name)
            or Path(entry.temporary).name != entry.temporary
            or not entry.temporary.startswith(RELEASE_STAGE_PREFIX)
            or entry.temporary == path.name
            or not all(
                isinstance(value, str) for value in (entry.previous, entry.staged)
            )
        ):
            raise ValueError("Invalid release intent file entry")
        return entry

    def stage_generation(self, path: Path) -> _Generation:
        generation = _generation(path)
        if generation.fingerprint() != self.staged or not _names_replacement(
            path, generation
        ):
            raise ReleaseError(f"Staged release file {path} changed.")
        return generation


@dataclass(frozen=True)
class _ReleaseIntent:
    version: str
    date: str
    changelog: _IntentFile
    project: _IntentFile
    rollback: _IntentFile | None = None


def _write_intent(path: Path, intent: _ReleaseIntent) -> None:
    with _stage_replacement(
        path, json.dumps(asdict(intent)).encode("utf-8"), private=True
    ) as (temporary, _):
        os.replace(temporary, path)
        _sync_release_names(f"Release recovery record {path} is", path)


def _retire_intent(path: Path) -> None:
    path.unlink()
    _sync_release_names("Release recovery record removal is", path)


def _recover_release(
    record: Path, changelog: Path, project: Path
) -> tuple[str, str] | None:
    try:
        info = record.lstat()
    except FileNotFoundError:
        return None

    conflict = (
        f"Release recovery conflict at {record}; inspect {changelog} and "
        f"{project} and reconcile them before removing the record."
    )
    try:
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Release intent must be a regular file")
        data = json.loads(_generation(record).content)
        first = _IntentFile.load(data["changelog"], changelog)
        second = _IntentFile.load(data["project"], project)
        rollback = (
            _IntentFile.load(data["rollback"], changelog)
            if data["rollback"] is not None
            else None
        )
        version, release_date = data["version"], data["date"]
        _validate_version(version)
        _validate_release_date(release_date)
        entries = {changelog: first, project: second}
        current = {path: _generation(path) for path in entries}
        current_changelog, current_project = (
            current[changelog].fingerprint(),
            current[project].fingerprint(),
        )
        canceled = current_project == second.previous and (
            current_changelog == first.previous
            or (rollback is not None and current_changelog == rollback.staged)
        )
        replacements: dict[Path, Path] = {}
        if not canceled:
            # Validate the entire pair before changing either surviving file.
            for path, entry in entries.items():
                fingerprint = current[path].fingerprint()
                if fingerprint == entry.staged and _names_replacement(
                    path, current[path]
                ):
                    continue
                if fingerprint != entry.previous:
                    raise ReleaseError(f"Release file {path} changed.")
                temporary = path.parent / entry.temporary
                replacements[path] = temporary
    except (OSError, ValueError, TypeError, KeyError, ReleaseError) as exc:
        raise ReleaseError(conflict) from exc

    for path, temporary in replacements.items():
        try:
            for target, generation in current.items():
                _require_generation(target, generation)
            entries[path].stage_generation(temporary)
        except (OSError, ReleaseError) as exc:
            raise ReleaseError(conflict) from exc
        try:
            os.replace(temporary, path)
        except OSError as exc:
            raise ReleaseError(
                f"Could not resume release renames; retry preparation with {record} in place."
            ) from exc

    _sync_release_names("Release files after recovery are", changelog, project)
    # Retain any foreign files occupying a stage name.
    stages = list(entries.items())
    if rollback is not None:
        stages.append((changelog, rollback))
    for path, entry in stages:
        temporary = path.parent / entry.temporary
        try:
            entry.stage_generation(temporary)
        except (OSError, ReleaseError):
            continue
        temporary.unlink()
    _retire_intent(record)
    return None if canceled else (version, release_date)


def prepare_release(
    changelog_path: Path,
    pyproject_path: Path,
    *,
    version: str,
    release_date: str | None = None,
) -> str:
    normalized_date = _validate_release_date(release_date or date.today().isoformat())
    # Keep this inode: unlinking a lock lets later waiters lock a different file.
    lock_path = changelog_path.parent.resolve() / ".kptncook-release.lock"
    record = lock_path.with_name(RELEASE_INTENT_NAME)
    for source in (changelog_path, pyproject_path):
        if source.parent.resolve() / source.name in (lock_path, record):
            raise ReleaseError(f"Reserved path for release source: {source}.")

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
            recovered = _recover_release(record, changelog_path, pyproject_path)
            if recovered is not None:
                recovered_version, recovered_date = recovered
                if recovered_version != version:
                    raise ReleaseError(
                        f"Recovered release {recovered_version} dated {recovered_date}. "
                        f"Review the prepared files before requesting {version} again."
                    )
                return recovered_date
            original_changelog = _generation(changelog_path)
            original_project = _generation(pyproject_path)
            changelog = _updated_changelog(
                original_changelog.content.decode("utf-8"),
                changelog_path,
                version=version,
                release_date=normalized_date,
            )
            project = _updated_pyproject(
                original_project.content.decode("utf-8"),
                pyproject_path,
                version=version,
            )
            with (
                _stage_replacement(
                    changelog_path, changelog.encode("utf-8"), retain_for=record
                ) as (changelog_temp, owned_changelog),
                _stage_replacement(
                    pyproject_path, project.encode("utf-8"), retain_for=record
                ) as (project_temp, owned_project),
            ):
                intent = _ReleaseIntent(
                    version,
                    normalized_date,
                    _IntentFile.capture(
                        changelog_path,
                        changelog_temp,
                        original_changelog,
                        owned_changelog,
                    ),
                    _IntentFile.capture(
                        pyproject_path, project_temp, original_project, owned_project
                    ),
                )
                _sync_release_names(
                    "Staged release files are", changelog_temp, project_temp
                )
                _write_intent(record, intent)

                def register_rollback(temporary: Path, owned: _Generation) -> None:
                    rollback = _IntentFile.capture(
                        changelog_path, temporary, owned_changelog, owned
                    )
                    _write_intent(record, replace(intent, rollback=rollback))

                changelog_published = False
                try:
                    _require_generation(pyproject_path, original_project)
                    _require_generation(changelog_path, original_changelog)
                    intent.changelog.stage_generation(changelog_temp)
                    os.replace(changelog_temp, changelog_path)
                    changelog_published = True
                    _require_generation(changelog_path, owned_changelog)
                    _require_generation(pyproject_path, original_project)
                    intent.project.stage_generation(project_temp)
                    os.replace(project_temp, pyproject_path)
                except (OSError, ReleaseError, KeyboardInterrupt):
                    if _names_replacement(pyproject_path, owned_project):
                        _sync_release_names(
                            "Prepared release files are", changelog_path, pyproject_path
                        )
                    elif changelog_published or _names_replacement(
                        changelog_path, owned_changelog
                    ):
                        _restore_changelog(
                            changelog_path,
                            original_changelog,
                            owned_changelog,
                            register_rollback,
                        )
                    _retire_intent(record)
                    raise
                _sync_release_names(
                    "Prepared release files are", changelog_path, pyproject_path
                )
                _retire_intent(record)
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
