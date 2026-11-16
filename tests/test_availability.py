from dataclasses import replace
from datetime import timedelta

import pytest

from uerj_monitor.availability import Change, compare_observations
from uerj_monitor.models import ClassObservation, ParseStatus, SourcePhase, VacancyNumbers, Watch


@pytest.fixture
def previous(captured_at):
    return ClassObservation(
        watch=Watch("TEST001", "01"),
        captured_at=captured_at,
        numbers=VacancyNumbers(25, 25, 30, 18, 10),
    )


def next_observation(previous, **changes):
    return replace(previous, captured_at=previous.captured_at + timedelta(minutes=1), **changes)


def test_first_capture_is_baseline(previous):
    assert compare_observations(None, previous) == Change.BASELINE


def test_repeated_numbers_are_not_a_new_event(previous):
    assert compare_observations(previous, next_observation(previous)) == Change.UNCHANGED


def test_changed_occupancy_is_only_a_numbers_event(previous):
    current = next_observation(previous, numbers=replace(previous.numbers, occupied=24))
    assert compare_observations(previous, current) == Change.NUMBERS_CHANGED


def test_requests_alone_do_not_mean_availability(previous):
    current = next_observation(previous, numbers=replace(previous.numbers, requested_total=3))
    assert compare_observations(previous, current) == Change.NUMBERS_CHANGED
    assert current.numbers.occupied == 25


@pytest.mark.parametrize("old,new", [(25, None), (None, 25), (None, None)])
def test_loss_or_recovery_of_data_is_not_a_vacancy_event(previous, old, new):
    before = replace(previous, numbers=replace(previous.numbers, occupied=old))
    after = next_observation(
        before,
        numbers=replace(before.numbers, occupied=new),
        parse_status=ParseStatus.PARTIAL if new is None else ParseStatus.OK,
    )
    assert compare_observations(before, after) == Change.INCOMPARABLE


def test_known_fields_can_still_change_in_partial_observations(previous):
    before = replace(previous, numbers=replace(previous.numbers, occupied=None))
    after = next_observation(before, numbers=replace(before.numbers, requested_total=3))
    assert compare_observations(before, after) == Change.NUMBERS_CHANGED


def test_phase_change_requires_a_new_baseline(previous):
    current = next_observation(previous, source_phase=SourcePhase.SAID)
    assert compare_observations(previous, current) == Change.INCOMPARABLE


@pytest.mark.parametrize("minutes", [0, -1])
def test_old_or_equal_timestamp_is_not_a_new_event(previous, minutes):
    current = replace(
        previous,
        captured_at=previous.captured_at + timedelta(minutes=minutes),
        numbers=replace(previous.numbers, occupied=24),
    )
    assert compare_observations(previous, current) == Change.INCOMPARABLE


@pytest.mark.parametrize(
    "watch", [Watch("OTHER", "01"), Watch("TEST001", "02"), Watch("TEST001", "01", "EXTERNA")]
)
def test_different_watch_cannot_be_compared(previous, watch):
    with pytest.raises(ValueError):
        compare_observations(previous, next_observation(previous, watch=watch))
