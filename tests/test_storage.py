import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest

from uerj_monitor.availability import Change
from uerj_monitor.models import ClassObservation, ParseStatus, SourcePhase, VacancyNumbers, Watch
from uerj_monitor.storage import EventKind, SQLiteStore


@pytest.fixture
def observation(captured_at):
    return ClassObservation(Watch("TEST001", "01"), captured_at, VacancyNumbers(25, 25, 30, 18, 10))


def next_observation(before, **changes):
    return replace(before, captured_at=before.captured_at + timedelta(minutes=1), **changes)


def test_state_and_deduplication_survive_restart(tmp_path, observation):
    path = tmp_path / "nested" / "monitor.sqlite3"
    current = next_observation(observation, numbers=replace(observation.numbers, occupied=24))
    with SQLiteStore(path) as store:
        assert store.record(observation).change == Change.BASELINE
        result = store.record(current)
        assert result.event.kind == EventKind.NUMBERS_CHANGED
        assert len(store.list_events()) == 1
    with SQLiteStore(path) as store:
        assert store.load_latest(observation.watch) == current
        assert store.record(next_observation(current)).event is None
        event = store.list_events()[0]
        assert event.previous == observation
        assert event.current == current


def test_out_of_order_capture_never_replaces_latest_state(observation):
    current = next_observation(observation, numbers=replace(observation.numbers, occupied=24))
    with SQLiteStore(":memory:") as store:
        store.record(current)
        assert store.record(observation).change == Change.INCOMPARABLE
        assert store.load_latest(observation.watch) == current
        assert store.list_events() == []


def test_loss_and_recovery_of_counts_does_not_emit_numeric_event(observation):
    missing = next_observation(
        observation,
        numbers=replace(observation.numbers, occupied=None),
        parse_status=ParseStatus.PARTIAL,
    )
    recovered = next_observation(missing, numbers=observation.numbers, parse_status=ParseStatus.OK)
    with SQLiteStore(":memory:") as store:
        store.record(observation)
        assert store.record(missing).event is None
        assert store.record(recovered).event is None
        assert store.list_events() == []


def test_unknown_does_not_rearm_a_previously_available_state(observation):
    with SQLiteStore(":memory:") as store:
        for index, value in enumerate((False, True, None, True)):
            current = replace(
                observation, captured_at=observation.captured_at + timedelta(minutes=index)
            )
            store.record(current, available=value, policy_id="simulation-v1")
        assert len(store.list_events()) == 1


@pytest.mark.parametrize("change", ["phase", "policy"])
def test_context_change_resets_availability_baseline(observation, change):
    current = next_observation(
        observation, source_phase=SourcePhase.SAID if change == "phase" else SourcePhase.UNKNOWN
    )
    with SQLiteStore(":memory:") as store:
        store.record(observation, available=False, policy_id="simulation-v1")
        result = store.record(
            current,
            available=True,
            policy_id="simulation-v2" if change == "policy" else "simulation-v1",
        )
        assert result.event is None


def test_failed_event_insert_rolls_back_the_state(observation):
    current = next_observation(observation, numbers=replace(observation.numbers, occupied=24))
    with SQLiteStore(":memory:") as store:
        store.record(observation)
        store.connection.execute("""
            CREATE TRIGGER fail_event BEFORE INSERT ON events
            BEGIN SELECT RAISE(ABORT, 'simulation'); END
        """)
        with pytest.raises(sqlite3.IntegrityError):
            store.record(current)
        assert store.load_latest(observation.watch) == observation
        assert store.list_events() == []
        store.connection.execute("DROP TRIGGER fail_event")
        assert store.record(current).event is not None


def test_unversioned_availability_is_rejected(observation):
    with SQLiteStore(":memory:") as store:
        with pytest.raises(ValueError):
            store.record(observation, available=True)


def test_future_schema_is_not_overwritten(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(ValueError, match="Versão"):
        SQLiteStore(path)
