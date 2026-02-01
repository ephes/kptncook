from kptncook.exporter_utils import (
    expand_timer_placeholders,
    format_timer,
    get_step_text,
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
            == "Fry 3 Min., then simmer 20–30 Min.."
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
