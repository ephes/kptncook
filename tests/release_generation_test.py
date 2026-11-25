"""Offline release preparation must retain competing generations."""

import multiprocessing
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from kptncook import release


def files(tmp_path):
    changelog, project = tmp_path / "CHANGELOG.md", tmp_path / "pyproject.toml"
    changelog.write_bytes(b"Unreleased\n==========\n\n- Invented notes.\n")
    project.write_bytes(b'[project]\nversion = "0.0.1"\n')
    return changelog, project


@pytest.mark.parametrize("edited", ["changelog", "project"])
def test_preflight_edit_is_not_overwritten(tmp_path, monkeypatch, edited):
    changelog, project = files(tmp_path)
    target = changelog if edited == "changelog" else project
    original = release._updated_pyproject

    def edit(*args, **kwargs):
        result = original(*args, **kwargs)
        target.write_bytes(target.read_bytes() + b"# external edit\n")
        return result

    monkeypatch.setattr(release, "_updated_pyproject", edit)
    with pytest.raises(release.ReleaseError, match="changed"):
        release.prepare_release(changelog, project, version="0.0.2")
    assert changelog.read_bytes().startswith(
        b"Unreleased\n==========\n\n- Invented notes."
    )
    assert target.read_bytes().endswith(b"# external edit\n")


def test_failed_second_write_cannot_compensate_external_edit(tmp_path, monkeypatch):
    changelog, project = files(tmp_path)
    original_changelog = changelog.read_bytes()
    original_replace = release.os.replace
    edited = b"Unreleased\n==========\n\n- New editor notes.\n"

    def fail(source, target):
        if target == project:
            changelog.write_bytes(edited)
            raise OSError("injected project failure")
        return original_replace(source, target)

    monkeypatch.setattr(release.os, "replace", fail)
    with pytest.raises(release.ReleaseError, match="recovery copy") as error:
        release.prepare_release(changelog, project, version="0.0.2")
    assert changelog.read_bytes() == edited
    recovery = list(tmp_path.glob("CHANGELOG.md.recovery-*"))
    assert len(recovery) == 1
    assert recovery[0].read_bytes() == original_changelog
    assert str(recovery[0]) in str(error.value)


def prepare_process(directory, started, entered, resume, results):
    from kptncook import release

    original = release._updated_changelog

    def pause(*args, **kwargs):
        entered.set()
        assert resume.wait(10)
        return original(*args, **kwargs)

    release._updated_changelog = pause
    try:
        started.set()
        release.prepare_release(
            Path(directory) / "CHANGELOG.md",
            Path(directory) / "pyproject.toml",
            version="0.0.2",
        )
        results.put("ok")
    except release.ReleaseError:
        results.put("refused")


def test_preparations_serialize_before_reading(tmp_path):
    files(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    started = [ctx.Event(), ctx.Event()]
    entered = [ctx.Event(), ctx.Event()]
    resume = ctx.Event()
    results = ctx.Queue()
    processes = [
        ctx.Process(
            target=prepare_process,
            args=(str(tmp_path), started[i], event, resume, results),
        )
        for i, event in enumerate(entered)
    ]
    try:
        processes[0].start()
        assert entered[0].wait(10)
        processes[1].start()
        assert started[1].wait(10)
        assert not entered[1].wait(0.5)
        resume.set()
        for process in processes:
            process.join(10)
            assert process.exitcode == 0
        assert sorted(results.get(timeout=2) for _ in processes) == ["ok", "refused"]
    finally:
        resume.set()
        for process in processes:
            if process.pid and process.is_alive():
                process.terminate()
                process.join(5)
        results.close()
        results.join_thread()


@pytest.mark.parametrize(
    "assignment",
    [
        'version = "0.0.1" # keep comment',
        "'version' = '0.0.1' # keep comment",
        '"version"= "0.0.1"',
    ],
)
def test_version_is_bound_to_project_table(tmp_path, assignment):
    changelog, project = files(tmp_path)
    text = (
        '[tool.demo]\nversion = "9.9.9"\n\n[project] # package\n'
        + assignment
        + '\n[tool.other]\nversion = "8.8.8"\n'
    )
    project.write_text(text)
    release.prepare_release(changelog, project, version="0.0.2")
    assert project.read_text() == text.replace("0.0.1", "0.0.2")


@pytest.mark.parametrize(
    "text",
    [
        '[tool.demo]\nversion = "0.0.1"\n[project]\nname = "demo"\n',
        "[project]\nversion = 42\n",
        'project = {version = "0.0.1"}\n',
        '[project]\nversion = """0.0.1"""\n',
    ],
)
def test_unsupported_project_layout_preserves_both_files(tmp_path, text):
    changelog, project = files(tmp_path)
    project.write_text(text)
    before = changelog.read_bytes(), project.read_bytes()
    with pytest.raises(release.ReleaseError, match="Could not update version"):
        release.prepare_release(changelog, project, version="0.0.2")
    assert (changelog.read_bytes(), project.read_bytes()) == before


def test_edit_after_first_publication_prevents_second_publication(
    tmp_path, monkeypatch
):
    changelog, project = files(tmp_path)
    originals = changelog.read_bytes(), project.read_bytes()
    replace = release.os.replace
    edited = b"Unreleased\n==========\n\n- Competing notes.\n"

    def edit(source, target):
        replace(source, target)
        if target == changelog:
            changelog.write_bytes(edited)

    monkeypatch.setattr(release.os, "replace", edit)
    with pytest.raises(release.ReleaseError, match="recovery copy"):
        release.prepare_release(changelog, project, version="0.0.2")
    assert changelog.read_bytes() == edited
    assert project.read_bytes() == originals[1]
    assert next(tmp_path.glob("CHANGELOG.md.recovery-*")).read_bytes() == originals[0]


def test_release_wait_is_bounded(tmp_path, monkeypatch):
    import time

    files(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    started, entered, resume = ctx.Event(), ctx.Event(), ctx.Event()
    results = ctx.Queue()
    process = ctx.Process(
        target=prepare_process, args=(str(tmp_path), started, entered, resume, results)
    )
    try:
        process.start()
        assert entered.wait(10)
        monkeypatch.setattr(release, "RELEASE_LOCK_TIMEOUT_SECONDS", 0.1)
        begin = time.monotonic()
        with pytest.raises(release.ReleaseError, match="busy"):
            release.prepare_release(
                tmp_path / "CHANGELOG.md", tmp_path / "pyproject.toml", version="0.0.3"
            )
        assert time.monotonic() - begin < 1.5
        resume.set()
        process.join(10)
        assert process.exitcode == 0
        assert results.get(timeout=2) == "ok"
    finally:
        resume.set()
        if process.is_alive():
            process.terminate()
            process.join(5)
        results.close()
        results.join_thread()


@pytest.mark.parametrize(
    "interruption", ["after_changelog", "before_project", "after_project"]
)
def test_interrupted_publication_leaves_consistent_pair(
    tmp_path, monkeypatch, interruption
):
    changelog, project = files(tmp_path)
    originals = changelog.read_bytes(), project.read_bytes()
    replace = release.os.replace
    interrupted = False

    def interrupt(source, target):
        nonlocal interrupted
        if (
            not interrupted
            and target == changelog
            and interruption == "after_changelog"
        ):
            interrupted = True
            replace(source, target)
            raise KeyboardInterrupt
        if target == project:
            if interruption == "after_project":
                replace(source, target)
            raise KeyboardInterrupt
        replace(source, target)

    monkeypatch.setattr(release.os, "replace", interrupt)
    with pytest.raises(KeyboardInterrupt):
        release.prepare_release(
            changelog, project, version="0.0.2", release_date="2026-11-24"
        )

    if interruption == "after_project":
        assert changelog.read_bytes() == (
            b"Unreleased\n==========\n\n0.0.2 - 2026-11-24\n==================\n"
            b"\n- Invented notes.\n"
        )
        assert project.read_bytes() == b'[project]\nversion = "0.0.2"\n'
    else:
        assert (changelog.read_bytes(), project.read_bytes()) == originals
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        ".kptncook-release.lock",
        "CHANGELOG.md",
        "pyproject.toml",
    ]


@pytest.mark.parametrize("oversized", ["changelog", "project"])
def test_prepare_refuses_oversized_release_input(tmp_path, oversized):
    changelog, project = files(tmp_path)
    target = changelog if oversized == "changelog" else project
    with target.open("ab") as stream:
        stream.write(b"#" + b"x" * (1024 * 1024) + b"\n")
    originals = changelog.read_bytes(), project.read_bytes()
    inodes = changelog.stat().st_ino, project.stat().st_ino

    with pytest.raises(release.ReleaseError, match=rf"{target.name}.*1 MiB"):
        release.prepare_release(changelog, project, version="0.0.2")

    assert (changelog.read_bytes(), project.read_bytes()) == originals
    assert (changelog.stat().st_ino, project.stat().st_ino) == inodes


def test_release_notes_refuse_oversized_changelog(tmp_path):
    changelog, _ = files(tmp_path)
    changelog.write_bytes(
        b"0.0.2 - 2026-11-24\n==================\n\n" + b"x" * (1024 * 1024)
    )
    with pytest.raises(release.ReleaseError, match=r"CHANGELOG.md.*1 MiB"):
        release.render_release_notes(changelog, version="0.0.2")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX named pipes")
@pytest.mark.parametrize("command", ["prepare", "notes"])
def test_release_command_refuses_fifo_without_waiting(tmp_path, command):
    changelog, project = files(tmp_path)
    fifo, other = (project, changelog) if command == "prepare" else (changelog, project)
    original = other.read_bytes()
    original_inode = other.stat().st_ino
    fifo.unlink()
    os.mkfifo(fifo)
    fifo_inode = fifo.stat().st_ino

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "kptncook.release",
            "--changelog",
            str(changelog),
            "--pyproject",
            str(project),
            command,
            "0.0.2",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 1
    assert "must be a regular file" in result.stderr
    assert (other.read_bytes(), other.stat().st_ino) == (original, original_inode)
    assert stat.S_ISFIFO(fifo.stat().st_mode)
    assert fifo.stat().st_ino == fifo_inode


def test_prepare_refuses_growth_after_descriptor_size_check(tmp_path, monkeypatch):
    changelog, project = files(tmp_path)
    original_changelog = changelog.read_bytes()
    original_project = project.read_bytes()
    project_inode = project.stat().st_ino
    fstat = os.fstat
    growth = b"#" + b"x" * (1024 * 1024) + b"\n"
    grew = False

    def grow(fd):
        nonlocal grew
        info = fstat(fd)
        if info.st_ino == project_inode and not grew:
            grew = True
            with project.open("ab") as stream:
                stream.write(growth)
        return info

    monkeypatch.setattr(release.os, "fstat", grow)
    with pytest.raises(release.ReleaseError, match=r"pyproject.toml.*1 MiB"):
        release.prepare_release(changelog, project, version="0.0.2")

    assert changelog.read_bytes() == original_changelog
    assert project.read_bytes() == original_project + growth


@pytest.mark.parametrize("short_write", ["changelog", "project", "recovery"])
def test_prepare_rejects_short_writes(tmp_path, monkeypatch, short_write):
    changelog, project = files(tmp_path)
    originals = changelog.read_bytes(), project.read_bytes()
    edited = b"Unreleased\n==========\n\n- Competing notes.\n"
    temporary = release.tempfile.NamedTemporaryFile
    replace = release.os.replace
    stage_count = 0

    def short_temporary(*args, **kwargs):
        nonlocal stage_count
        stream = temporary(*args, **kwargs)
        stage_count += 1
        if (
            (short_write == "changelog" and stage_count == 1)
            or (short_write == "project" and stage_count == 2)
            or (
                short_write == "recovery"
                and kwargs.get("prefix", "").endswith(".recovery-")
            )
        ):
            write = stream.write
            stream.write = lambda data: write(data[:-1])
        return stream

    def fail_project(source, target):
        if short_write == "recovery" and target == project:
            changelog.write_bytes(edited)
            raise OSError("injected project failure")
        replace(source, target)

    monkeypatch.setattr(release.tempfile, "NamedTemporaryFile", short_temporary)
    monkeypatch.setattr(release.os, "replace", fail_project)
    with pytest.raises(release.ReleaseError, match="Incomplete .*write"):
        release.prepare_release(changelog, project, version="0.0.2")

    assert changelog.read_bytes() == (
        edited if short_write == "recovery" else originals[0]
    )
    assert project.read_bytes() == originals[1]
    assert {path.name for path in tmp_path.iterdir()} == {
        ".kptncook-release.lock",
        "CHANGELOG.md",
        "pyproject.toml",
    }


@pytest.mark.parametrize(
    "scenario",
    [
        "pair",
        "pair_changelog_sync",
        "pair_project_sync",
        "single",
        "rollback",
        "recovery",
    ],
)
def test_release_requires_directory_sync(tmp_path, monkeypatch, scenario):
    changelog, project = files(tmp_path)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    project = project.rename(project_dir / project.name)
    originals = changelog.read_bytes(), project.read_bytes()
    edited = b"Unreleased\n==========\n\n- Competing notes.\n"
    replace = release.os.replace
    fsync = release.os.fsync
    directory_changes = []
    directory_syncs = []
    directories = {path.stat().st_ino: path for path in (tmp_path, project_dir)}

    def publish(source, target):
        if target == project and scenario in {"rollback", "recovery"}:
            if scenario == "recovery":
                changelog.write_bytes(edited)
            raise OSError("injected project failure")
        replace(source, target)
        directory_changes.append(target.parent)

    def sync(fd):
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode):
            directory = directories[info.st_ino]
            directory_syncs.append(directory)
            if scenario == "pair_project_sync" and directory == project_dir:
                raise OSError("injected project directory sync failure")
            if scenario not in {"pair", "pair_project_sync"} and directory == tmp_path:
                if scenario == "rollback":
                    assert directory_changes == [tmp_path, tmp_path]
                    assert changelog.read_bytes() == originals[0]
                raise OSError("injected changelog directory sync failure")
        fsync(fd)

    monkeypatch.setattr(release.os, "replace", publish)
    monkeypatch.setattr(release.os, "fsync", sync)

    def prepare():
        if scenario == "single":
            release.update_changelog_for_release(
                changelog, version="0.0.2", release_date="2026-11-24"
            )
        else:
            release.prepare_release(changelog, project, version="0.0.2")

    if scenario == "pair":
        prepare()
    else:
        with pytest.raises(release.ReleaseError, match="visible.*durability") as error:
            prepare()
        if scenario == "recovery":
            recovery = next(tmp_path.glob("CHANGELOG.md.recovery-*"))
            assert recovery.read_bytes() == originals[0]
            assert str(recovery) in str(error.value)
        else:
            assert not list(tmp_path.glob("CHANGELOG.md.recovery-*"))

    if scenario.startswith("pair"):
        assert directory_changes == directory_syncs == [tmp_path, project_dir]
        assert b"0.0.2 - " in changelog.read_bytes()
        assert b'"0.0.2"' in project.read_bytes()
    else:
        assert directory_syncs == [tmp_path]
        assert project.read_bytes() == originals[1]
        if scenario == "rollback":
            assert changelog.read_bytes() == originals[0]
        elif scenario == "recovery":
            assert changelog.read_bytes() == edited
        else:
            assert b"0.0.2 - " in changelog.read_bytes()
