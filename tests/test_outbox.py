import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest

from uerj_monitor.models import ClassObservation, VacancyNumbers, Watch
from uerj_monitor.notifier import TelegramCredentials, TelegramError
from uerj_monitor.outbox import DeliveryWorker, Outbox
from uerj_monitor.storage import SQLiteStore

CREDENTIALS = TelegramCredentials("123456789:synthetic_test_token", "42")


class FakeTelegram:
    credentials = CREDENTIALS

    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.messages = []

    def send_message(self, text):
        self.messages.append(text)
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeClock:
    def __init__(self):
        self.now = 1000
        self.waits = []

    def __call__(self):
        return self.now

    def wait(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


def add_events(store, captured_at, count=1, destination=CREDENTIALS.destination):
    observation = ClassObservation(
        Watch("TEST001", "01"), captured_at, VacancyNumbers(25, 25, 30, 18, 10)
    )
    assert store.record(observation, delivery_destination=destination).event is None
    events = []
    for index in range(count):
        observation = replace(
            observation,
            captured_at=observation.captured_at + timedelta(minutes=1),
            numbers=replace(observation.numbers, occupied=24 - index),
        )
        events.append(store.record(observation, delivery_destination=destination).event)
    return events


def worker(store, outcomes, clock):
    return DeliveryWorker(Outbox(store), FakeTelegram(outcomes), clock=clock, wait=clock.wait)


def test_successful_deliveries_are_paced_and_not_repeated_after_restart(tmp_path, captured_at):
    path = tmp_path / "monitor.sqlite3"
    clock = FakeClock()
    with SQLiteStore(path) as store:
        add_events(store, captured_at, count=2)
        assert worker(store, [11, 12], clock).deliver_due() == {"sent": 2, "errors": 0}
        assert all(item["status"] == "sent" for item in Outbox(store).list_deliveries())
        assert clock.waits[0] == 3
    with SQLiteStore(path) as store:
        assert worker(store, [], clock).deliver_due() == {"sent": 0, "errors": 0}


def test_retry_is_deferred_and_limited_to_three_attempts(captured_at):
    clock = FakeClock()
    error = TelegramError("connection_error", retryable=True)
    with SQLiteStore(":memory:") as store:
        add_events(store, captured_at)
        sender = worker(store, [error, error, error], clock)
        assert sender.deliver_due()["errors"] == 1
        assert sender.deliver_due() == {"sent": 0, "errors": 0}
        clock.now += 60
        assert sender.deliver_due()["errors"] == 1
        clock.now += 120
        assert sender.deliver_due()["errors"] == 1
        item = Outbox(store).list_deliveries()[0]
        assert item["status"] == "failed"
        assert item["attempts"] == 3
        clock.now += 1000
        assert sender.deliver_due() == {"sent": 0, "errors": 0}


def test_retry_after_blocks_other_events_and_survives_restart(tmp_path, captured_at):
    path = tmp_path / "monitor.sqlite3"
    clock = FakeClock()
    with SQLiteStore(path) as store:
        add_events(store, captured_at, count=2)
        error = TelegramError("rate_limited", retryable=True, retry_after=1200)
        assert worker(store, [error], clock).deliver_due()["errors"] == 1
    with SQLiteStore(path) as store:
        clock.now = 2199
        assert worker(store, [], clock).deliver_due() == {"sent": 0, "errors": 0}
        clock.now = 2200
        assert worker(store, [11, 12], clock).deliver_due()["sent"] == 2


def test_success_cooldown_is_persistent_between_worker_instances(captured_at):
    clock = FakeClock()
    with SQLiteStore(":memory:") as store:
        add_events(store, captured_at, count=2)
        assert worker(store, [11], clock).deliver_due(limit=1)["sent"] == 1
        assert worker(store, [], clock).deliver_due(limit=1)["sent"] == 0
        clock.now += 3
        assert worker(store, [12], clock).deliver_due(limit=1)["sent"] == 1


def test_ambiguous_delivery_requires_manual_retry(captured_at):
    clock = FakeClock()
    with SQLiteStore(":memory:") as store:
        event = add_events(store, captured_at)[0]
        error = TelegramError("delivery_uncertain", uncertain=True)
        assert worker(store, [error], clock).deliver_due()["errors"] == 1
        assert Outbox(store).list_deliveries()[0]["status"] == "uncertain"
        assert worker(store, [], clock).deliver_due()["sent"] == 0
        assert Outbox(store).retry(event.id, CREDENTIALS.destination, clock()) is True
        assert worker(store, [11], clock).deliver_due()["sent"] == 1
        assert Outbox(store).retry(event.id, CREDENTIALS.destination, clock()) is False


def test_interruption_marks_inflight_delivery_as_uncertain(captured_at):
    clock = FakeClock()
    with SQLiteStore(":memory:") as store:
        add_events(store, captured_at)
        with pytest.raises(KeyboardInterrupt):
            worker(store, [KeyboardInterrupt()], clock).deliver_due()
        assert Outbox(store).list_deliveries()[0]["status"] == "uncertain"


def test_hard_crash_claim_is_not_reclaimed_automatically(tmp_path, captured_at):
    path = tmp_path / "monitor.sqlite3"
    clock = FakeClock()
    with SQLiteStore(path) as store:
        add_events(store, captured_at, count=2)
        assert Outbox(store).claim(CREDENTIALS.destination, clock()) is not None
    with SQLiteStore(path) as store:
        assert worker(store, [], clock).deliver_due()["sent"] == 0
        assert any(item["status"] == "sending" for item in Outbox(store).list_deliveries())


def test_new_recipient_cannot_receive_or_retry_old_destinations(captured_at):
    with SQLiteStore(":memory:") as store:
        event = add_events(store, captured_at)[0]
        new_destination = TelegramCredentials(CREDENTIALS.token, "43").destination
        assert Outbox(store).claim(new_destination, 1000) is None
        assert Outbox(store).retry(event.id, new_destination, 1000) is False


def test_outbox_insert_failure_rolls_back_event_and_latest_state(captured_at):
    with SQLiteStore(":memory:") as store:
        observation = ClassObservation(
            Watch("TEST001", "01"), captured_at, VacancyNumbers(25, 25, 30, 18, 10)
        )
        store.record(observation)
        store.connection.execute("""
            CREATE TRIGGER fail_queue BEFORE INSERT ON deliveries
            BEGIN SELECT RAISE(ABORT, 'simulation'); END
        """)
        current = replace(
            observation,
            captured_at=captured_at + timedelta(minutes=1),
            numbers=replace(observation.numbers, occupied=24),
        )
        with pytest.raises(sqlite3.IntegrityError):
            store.record(current, delivery_destination=CREDENTIALS.destination)
        assert store.load_latest(observation.watch) == observation
        assert store.list_events() == []
        assert Outbox(store).list_deliveries() == []


def test_version_one_migration_keeps_history_without_backfilling_old_events(tmp_path, captured_at):
    path = tmp_path / "monitor.sqlite3"
    with SQLiteStore(path) as store:
        old_event = add_events(store, captured_at, destination=None)[0]
        store.connection.executescript("""
            DROP TABLE deliveries;
            DROP TABLE delivery_limits;
            PRAGMA user_version = 1;
        """)
    with SQLiteStore(path) as store:
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert store.list_events()[0] == old_event
        assert Outbox(store).list_deliveries() == []
