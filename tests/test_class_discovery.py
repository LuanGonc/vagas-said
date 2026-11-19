import json
import sqlite3
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest

from uerj_monitor.bot_monitor import SubscriptionMonitor
from uerj_monitor.models import ClassObservation, SourcePhase, VacancyNumbers, Watch
from uerj_monitor.notifier import TelegramClient, TelegramCredentials
from uerj_monitor.outbox import DeliveryWorker, Outbox
from uerj_monitor.portal_client import SourceError
from uerj_monitor.storage import EventKind, SQLiteStore
from uerj_monitor.subscriptions import SubscriptionRepository

CODE = "TEST001"
CREDENTIALS = TelegramCredentials("123456789:synthetic_test_token")


class Catalog:
    shared_queries = True

    def __init__(self):
        self.numbers = ["01", "02"]
        self.phase = SourcePhase.UNKNOWN
        self.fail = False
        self.calls = 0

    def observe(self, code, user_id, captured_at):
        self.calls += 1
        if self.fail:
            raise SourceError("Consulta indisponível.")
        return [
            ClassObservation(
                Watch(code, number, discipline_name="Disciplina de teste"),
                captured_at,
                VacancyNumbers(25, 25),
                self.phase,
            )
            for number in self.numbers
        ]


@pytest.fixture
def setup(tmp_path, captured_at):
    with SQLiteStore(tmp_path / "bot.sqlite3") as store:
        repository = SubscriptionRepository(store)
        for user_id in (1, 2):
            repository.grant(user_id, 1)
            repository.subscribe(user_id, Watch(CODE, "01"))
        catalog = Catalog()
        now = [captured_at]
        monitor = SubscriptionMonitor(store, catalog, CREDENTIALS, clock=lambda: now[0])
        yield store, repository, catalog, now, monitor


def advance(now):
    now[0] += timedelta(minutes=1)


def test_shared_discovery_delivers_once_per_user_without_adding_subscriptions(setup):
    store, repo, catalog, now, monitor = setup
    assert monitor.poll_once().events == ()
    assert catalog.calls == 1
    # Duas assinaturas da mesma disciplina não duplicam o aviso para a pessoa.
    repo.subscribe(1, Watch(CODE, "02"))
    catalog.numbers += ["03", "04"]
    advance(now)
    events = monitor.poll_once().events
    assert len(events) == 4
    assert all(event.kind == EventKind.CLASS_DISCOVERED for event in events)
    assert all(event.to_dict()["previous"] is None for event in events)
    assert catalog.calls == 2
    assert len(repo.list_active(1)) == 2
    assert len(repo.list_active(2)) == 1
    assert store.list_events() == list(reversed(events))
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent)}})

    moment = [1000]

    def wait(seconds):
        moment[0] += seconds

    with TelegramClient(CREDENTIALS, transport=httpx.MockTransport(handler)) as client:
        for user_id in (1, 2):
            worker = DeliveryWorker(
                Outbox(store),
                client.for_chat(user_id),
                subscription_user_id=user_id,
                clock=lambda: moment[0],
                wait=wait,
            )
            assert worker.deliver_due() == {"sent": 2, "errors": 0}
    assert [payload["chat_id"] for payload in sent] == ["1", "1", "2", "2"]
    assert all("Nova turma encontrada" in payload["text"] for payload in sent)
    assert all(f"/turmas {CODE}" in payload["text"] for payload in sent)
    assert all("não foi adicionada" in payload["text"] for payload in sent)
    assert all("não confirma vagas" in payload["text"] for payload in sent)
    advance(now)
    assert monitor.poll_once().events == ()


def test_new_user_gets_own_baseline_and_disappearance_does_not_rearm_alert(setup):
    store, repo, catalog, now, monitor = setup
    monitor.poll_once()
    catalog.numbers.append("03")
    advance(now)
    assert len(monitor.poll_once().events) == 2
    repo.grant(3, 1)
    repo.subscribe(3, Watch(CODE, "01"))
    advance(now)
    assert monitor.poll_once().events == ()
    catalog.numbers.remove("03")
    advance(now)
    assert monitor.poll_once().events == ()
    catalog.numbers.append("03")
    advance(now)
    assert monitor.poll_once().events == ()
    assert len(store.list_events()) == 2


def test_failed_or_empty_query_preserves_class_baseline(setup):
    store, _, catalog, now, monitor = setup
    monitor.poll_once()
    catalog.numbers = ["01", "02", "03"]
    catalog.fail = True
    advance(now)
    assert len(monitor.poll_once().failures) == 2
    catalog.fail = False
    catalog.numbers = []
    advance(now)
    assert monitor.poll_once().events == ()
    catalog.numbers = ["01", "02", "03"]
    advance(now)
    assert len(monitor.poll_once().events) == 2
    assert len(store.list_events()) == 2


def test_removed_watched_class_does_not_prevent_new_class_alert(setup):
    _, _, catalog, now, monitor = setup
    monitor.poll_once()
    catalog.numbers = ["02", "03"]
    advance(now)
    result = monitor.poll_once()
    assert len(result.events) == 2
    assert all(event.current.watch.class_number == "03" for event in result.events)
    assert len(result.failures) == 2


def test_removal_keeps_discipline_alert_until_last_subscription_and_resets_on_readd(setup):
    store, repo, catalog, now, monitor = setup
    repo.subscribe(1, Watch(CODE, "02"))
    monitor.poll_once()
    catalog.numbers.append("03")
    advance(now)
    monitor.poll_once()
    first, second = repo.list_active(1)
    repo.unsubscribe(1, first.id)
    # O evento é da disciplina: remover só uma turma não cancela o aviso.
    destination = replace(CREDENTIALS, chat_id="1").destination
    claimed = Outbox(store).claim(destination, 1000, subscription_user_id=1)
    assert claimed.event.kind == EventKind.CLASS_DISCOVERED
    Outbox(store).complete(claimed, 1, 1000)
    catalog.numbers.append("04")
    advance(now)
    assert len(monitor.poll_once().events) == 2
    repo.unsubscribe(1, second.id)
    cancelled = [row for row in Outbox(store).list_deliveries() if row["status"] == "cancelled"]
    assert len(cancelled) == 1
    assert Outbox(store).claim(destination, 1004, subscription_user_id=1) is None
    repo.subscribe(1, Watch(CODE, "01"))
    advance(now)
    assert monitor.poll_once().events == ()


def test_revocation_cancels_pending_discovery_without_affecting_other_users(setup):
    store, repo, catalog, now, monitor = setup
    monitor.poll_once()
    catalog.numbers.append("03")
    advance(now)
    monitor.poll_once()
    repo.revoke(1)
    assert sorted(row["status"] for row in Outbox(store).list_deliveries()) == [
        "cancelled",
        "pending",
    ]
    assert (
        Outbox(store).claim(
            replace(CREDENTIALS, chat_id="2").destination, 1000, subscription_user_id=1
        )
        is None
    )
    assert (
        Outbox(store).claim(
            replace(CREDENTIALS, chat_id="2").destination, 1000, subscription_user_id=2
        )
        is not None
    )
    repo.grant(1, 1)
    repo.subscribe(1, Watch(CODE, "01"))
    advance(now)
    assert monitor.poll_once().events == ()


def test_discovery_and_queue_roll_back_together_and_ignore_old_captures(setup):
    store, _, catalog, now, monitor = setup
    monitor.poll_once()
    old = now[0]
    catalog.numbers.append("03")
    advance(now)
    store.connection.executescript("""
        CREATE TRIGGER reject_delivery BEFORE INSERT ON deliveries
        BEGIN SELECT RAISE(ABORT, 'simulation'); END;
    """)
    with pytest.raises(sqlite3.IntegrityError):
        monitor.poll_once()
    assert store.list_events() == []
    store.connection.execute("DROP TRIGGER reject_delivery")
    assert len(monitor.poll_once().events) == 2
    now[0] = old
    catalog.numbers.append("04")
    assert monitor.poll_once().events == ()
    advance(now)
    advance(now)
    assert len(monitor.poll_once().events) == 2


def test_state_and_pending_deliveries_survive_version_three_migration_and_restart(
    tmp_path, captured_at
):
    path = tmp_path / "old.sqlite3"
    with SQLiteStore(path) as store:
        repo = SubscriptionRepository(store)
        repo.grant(1, 1)
        repo.subscribe(1, Watch(CODE, "01"))
        observation = ClassObservation(Watch(CODE, "01"), captured_at, VacancyNumbers(25, 25))
        store.record(observation)
        store.connection.executescript("""
            DROP TABLE discipline_events;
            DROP TABLE discipline_class_states;
            PRAGMA user_version = 3;
        """)
    catalog = Catalog()
    now = [captured_at]
    with SQLiteStore(path) as store:
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert store.load_latest(observation.watch) == observation
        monitor = SubscriptionMonitor(store, catalog, CREDENTIALS, clock=lambda: now[0])
        assert monitor.poll_once().events == ()
        catalog.numbers.append("03")
        advance(now)
        assert len(monitor.poll_once().events) == 1
    with SQLiteStore(path) as store:
        monitor = SubscriptionMonitor(store, catalog, CREDENTIALS, clock=lambda: now[0])
        advance(now)
        assert monitor.poll_once().events == ()
        event = store.list_events()[0]
        assert event.previous is None
        assert event.kind == EventKind.CLASS_DISCOVERED
        assert (
            Outbox(store)
            .claim(replace(CREDENTIALS, chat_id="1").destination, 1000, subscription_user_id=1)
            .event
            == event
        )


def test_phase_change_rebaselines_without_announcing_all_current_classes(setup):
    _, _, catalog, now, monitor = setup
    monitor.poll_once()
    catalog.phase = SourcePhase.SAID
    catalog.numbers.append("03")
    advance(now)
    assert monitor.poll_once().events == ()
    catalog.numbers.append("04")
    advance(now)
    assert len(monitor.poll_once().events) == 2


def test_discovery_does_not_consume_or_require_a_free_subscription_slot(setup):
    _, repo, catalog, now, monitor = setup
    catalog.numbers = [f"{number:02}" for number in range(1, 11)]
    for number in catalog.numbers:
        repo.subscribe(1, Watch(CODE, number))
    assert len(repo.list_active(1)) == 10
    monitor.poll_once()
    catalog.numbers.append("11")
    advance(now)
    assert len(monitor.poll_once().events) == 2
    assert len(repo.list_active(1)) == 10
