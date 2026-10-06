import pytest

from kptncook.exporter_utils import (
    expand_timer_placeholders,
    format_timer,
    get_step_text,
    move_to_free_target,
)
from kptncook.models import Image, LocalizedString, RecipeStep, StepTimer


class TestFormatTimer:
    def test_min_or_exact_only_default_german(self):
        assert format_timer(StepTimer(min_or_exact=15)) == "15 Min."

    def test_min_or_exact_and_max_default_german(self):
        assert format_timer(StepTimer(min_or_exact=30, max=40)) == "30–40 Min."

    def test_max_only_default_german(self):
        assert format_timer(StepTimer(max=20)) == "bis zu 20 Min."

    def test_empty_returns_empty_string(self):
        assert format_timer(StepTimer()) == ""


class TestExpandTimerPlaceholders:
    def test_single_placeholder(self):
        text = "Cook for ca. <timer> until done."
        timers = [StepTimer(min_or_exact=15)]
        assert (
            expand_timer_placeholders(text, timers)
            == "Cook for ca. 15 Min. until done."
        )

    def test_multiple_placeholders(self):
        text = "Fry <timer>, then simmer <timer>."
        timers = [
            StepTimer(min_or_exact=3),
            StepTimer(min_or_exact=20, max=30),
        ]
        assert (
            expand_timer_placeholders(text, timers)
            == "Fry 3 Min., then simmer 20–30 Min."
        )

    def test_no_placeholders_unchanged(self):
        text = "Just add salt."
        timers = [StepTimer(min_or_exact=5)]
        assert expand_timer_placeholders(text, timers) == "Just add salt."

    def test_no_timers_strips_placeholder(self):
        text = "Cook <timer> and serve."
        assert expand_timer_placeholders(text, None) == "Cook  and serve."
        assert expand_timer_placeholders(text, []) == "Cook  and serve."

    def test_more_placeholders_than_timers(self):
        text = "<timer> and <timer> and <timer>"
        timers = [StepTimer(min_or_exact=1), StepTimer(min_or_exact=2)]
        assert expand_timer_placeholders(text, timers) == "1 Min. and 2 Min. and "

    def test_empty_text(self):
        assert expand_timer_placeholders("", [StepTimer(min_or_exact=5)]) == ""


class TestGetStepText:
    def test_step_with_timer_expands_placeholder(self):
        step = RecipeStep(
            title=LocalizedString(de="Kartoffeln ca. <timer> kochen."),
            image=Image(name="x.jpg", url="https://example.com/x.jpg"),
            timers=[StepTimer(min_or_exact=15)],
        )
        assert get_step_text(step) == "Kartoffeln ca. 15 Min. kochen."

    def test_step_without_timers_strips_placeholder(self):
        step = RecipeStep(
            title=LocalizedString(de="Cook <timer> and serve."),
            image=Image(name="x.jpg", url="https://example.com/x.jpg"),
        )
        assert get_step_text(step) == "Cook  and serve."

    def test_step_old_format_no_placeholder(self):
        step = RecipeStep(
            title=LocalizedString(de="Ca. 2-3 min. braten."),
            image=Image(name="x.jpg", url="https://example.com/x.jpg"),
        )
        assert get_step_text(step) == "Ca. 2-3 min. braten."


def test_move_to_free_target_never_overwrites(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "name.zip").write_bytes(b"old")
    (out / "name-id1.zip").write_bytes(b"old too")

    def source(content: bytes):
        path = tmp_path / "src.zip"
        path.write_bytes(content)
        return path

    target = move_to_free_target(source(b"new"), out, "name", ".zip", "id1")
    assert target == out / "name-id1-2.zip"
    assert target.read_bytes() == b"new"
    assert not (tmp_path / "src.zip").exists()
    assert (out / "name.zip").read_bytes() == b"old"
    assert (out / "name-id1.zip").read_bytes() == b"old too"

    plain = move_to_free_target(source(b"plain"), out, "name", ".zip")
    assert plain == out / "name-2.zip"
    assert plain.read_bytes() == b"plain"


def test_move_to_free_target_keeps_existing_file_on_source_error(tmp_path):
    existing = tmp_path / "name.zip"
    existing.write_bytes(b"old")

    with pytest.raises(FileNotFoundError):
        move_to_free_target(tmp_path / "missing.zip", tmp_path, "name", ".zip")

    assert existing.read_bytes() == b"old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["name.zip"]


def test_move_to_free_target_removes_own_partial_target_on_copy_error(tmp_path, mocker):
    existing = tmp_path / "name.zip"
    existing.write_bytes(b"old")
    source = tmp_path / "src.bin"
    source.write_bytes(b"new")
    mocker.patch(
        "kptncook.exporter_utils.shutil.copyfileobj", side_effect=OSError("disk full")
    )

    with pytest.raises(OSError, match="disk full"):
        move_to_free_target(source, tmp_path, "name", ".zip", "id1")

    assert existing.read_bytes() == b"old"
    assert not (tmp_path / "name-id1.zip").exists()
    assert source.read_bytes() == b"new"
