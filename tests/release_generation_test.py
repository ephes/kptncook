"""Offline release preparation must retain competing generations."""

import multiprocessing
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
