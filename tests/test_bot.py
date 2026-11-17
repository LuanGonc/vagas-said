from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from uerj_monitor.bot import BotService
from uerj_monitor.bot_monitor import SubscriptionMonitor
from uerj_monitor.catalog import LocalCatalog
from uerj_monitor.cli import main
from uerj_monitor.config import load_config
from uerj_monitor.models import Watch
from uerj_monitor.notifier import TelegramCredentials
from uerj_monitor.outbox import Outbox
from uerj_monitor.storage import SQLiteStore
from uerj_monitor.subscriptions import SubscriptionRepository

EXAMPLE = Path(__file__).parents[1] / "config.bot.example.toml"
CODE = "IME01-06766"


def message(user_id, text):
    return {
        "message": {
            "from": {"id": user_id},
            "chat": {"id": user_id, "type": "private"},
            "text": text,
        }
    }


def callback(user_id, data):
    return {
        "callback_query": {
            "from": {"id": user_id},
            "id": "callback",
            "data": data,
            "message": {"chat": {"id": user_id, "type": "private"}},
        }
    }


@pytest.fixture
def setup(tmp_path):
    config = load_config(EXAMPLE, require_watches=False)
    html = tmp_path / "classes.html"
    html.write_text(config.sources[CODE].read_text(encoding="utf-8"), encoding="utf-8")
    config = replace(config, sources={CODE: html})
    with SQLiteStore(":memory:") as store:
        repository = SubscriptionRepository(store)
        service = BotService(repository, LocalCatalog(config), admin_user_ids=(1,))
        yield store, service, html


def choose_first(service, user_id, reply):
    button = reply.markup["inline_keyboard"][0][0]
    return service.handle(callback(user_id, button["callback_data"])).reply


def subscribe_first(service, user_id):
    search = service.handle(message(user_id, "/buscar calculo ii")).reply
    classes = choose_first(service, user_id, search)
    return choose_first(service, user_id, classes)


def test_authorized_search_maps_actual_local_classes_and_adds_without_duplicates(setup):
    _, service, _ = setup
    assert "1/10" in subscribe_first(service, 1).text
    assert "Suas turmas: 1/10" in service.handle(message(1, "/minhas")).reply.text
    assert "já acompanha" in subscribe_first(service, 1).text
    classes = service.handle(message(1, f"/turmas {CODE}")).reply
    labels = [row[0]["text"] for row in classes.markup["inline_keyboard"]]
    assert labels == ["Monitorar turma 01", "Monitorar turma 02"]


def test_only_admin_can_authorize_and_revoke(setup):
    _, service, _ = setup
    assert "precisa autorizar" in service.handle(message(2, "/start")).reply.text
    assert "2" in service.handle(message(2, "/id")).reply.text
    assert not service.repository.authorized(2)
    assert "autorizado" in service.handle(message(1, "/autorizar 2")).reply.text
    assert "1/10" in subscribe_first(service, 2).text
    assert "Somente o responsável" in service.handle(message(2, "/autorizar 3")).reply.text
    assert "revogado" in service.handle(message(1, "/revogar 2")).reply.text
    assert service.repository.list_active(2) == []


def test_buttons_and_removal_cannot_cross_users(setup):
    _, service, _ = setup
    service.handle(message(1, "/autorizar 2"))
    subscribe_first(service, 1)
    search = service.handle(message(1, "/buscar")).reply
    stolen = search.markup["inline_keyboard"][0][0]["callback_data"]
    assert "não pertence" in service.handle(callback(2, stolen)).reply.text
    first_id = service.repository.list_active(1)[0].id
    assert "não está ativa" in service.handle(message(2, f"/parar {first_id}")).reply.text
    assert len(service.repository.list_active(1)) == 1


def test_groups_and_private_chat_identity_mismatches_are_ignored(setup):
    _, service, _ = setup
    update = message(1, "/start")
    update["message"]["chat"] = {"id": -10, "type": "group"}
    assert service.handle(update) is None
    update["message"]["chat"] = {"id": 2, "type": "private"}
    assert service.handle(update) is None


def test_stale_class_button_is_revalidated_before_subscribing(setup):
    _, service, html = setup
    classes = choose_first(service, 1, service.handle(message(1, "/buscar")).reply)
    html.write_text(
        html.read_text(encoding="utf-8").replace("TURMA: 01", "TURMA: 99"), encoding="utf-8"
    )
    result = choose_first(service, 1, classes)
    assert "não aparece mais" in result.text
    assert service.repository.list_active(1) == []


def test_subscribers_have_independent_baselines_and_destinations(setup, captured_at):
    store, service, html = setup
    credentials = TelegramCredentials("123456789:synthetic_test_token")
    moment = captured_at
    monitor = SubscriptionMonitor(store, service.catalog, credentials, clock=lambda: moment)
    subscribe_first(service, 1)
    assert monitor.poll_once().events == ()
    html.write_text(
        html.read_text(encoding="utf-8").replace(
            "<td>25</td><td>25</td>", "<td>25</td><td>24</td>", 1
        ),
        encoding="utf-8",
    )
    moment += timedelta(minutes=1)
    assert len(monitor.poll_once().events) == 1
    service.handle(message(1, "/autorizar 2"))
    subscribe_first(service, 2)
    moment += timedelta(minutes=1)
    assert monitor.poll_once().events == ()
    html.write_text(
        html.read_text(encoding="utf-8").replace(
            "<td>25</td><td>24</td>", "<td>25</td><td>23</td>", 1
        ),
        encoding="utf-8",
    )
    moment += timedelta(minutes=1)
    assert len(monitor.poll_once().events) == 2
    assert len(service.repository.list_active(1)) == 1
    assert len(service.repository.list_active(2)) == 1
    queue = Outbox(store)
    owner1 = replace(credentials, chat_id="1").destination
    owner2 = replace(credentials, chat_id="2").destination
    assert queue.claim(owner1, 1000).event.id != queue.claim(owner2, 1000).event.id


def test_removal_cancels_only_owners_pending_notifications(setup, captured_at):
    store, service, html = setup
    service.handle(message(1, "/autorizar 2"))
    subscribe_first(service, 1)
    subscribe_first(service, 2)
    moment = captured_at
    monitor = SubscriptionMonitor(
        store,
        service.catalog,
        TelegramCredentials("123456789:synthetic_test_token"),
        clock=lambda: moment,
    )
    monitor.poll_once()
    html.write_text(
        html.read_text(encoding="utf-8").replace("<td>18</td>", "<td>3</td>"), encoding="utf-8"
    )
    moment += timedelta(minutes=1)
    assert len(monitor.poll_once().events) == 2
    subscription = service.repository.list_active(1)[0]
    service.handle(message(1, f"/parar {subscription.id}"))
    statuses = [row["status"] for row in Outbox(store).list_deliveries()]
    assert sorted(statuses) == ["cancelled", "pending"]
    assert len(service.repository.list_active(2)) == 1


def test_demo_runs_without_token_or_network(monkeypatch, capsys):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    assert main(["bot-demo", "--config", str(EXAMPLE)]) == 0
    output = capsys.readouterr().out
    assert "1/10" in output
    assert CODE in output


def test_all_classes_are_paginated_and_eleventh_selection_is_rejected(setup, monkeypatch):
    _, service, _ = setup
    monkeypatch.setattr(
        service.catalog,
        "classes",
        lambda code, user_id: [Watch(code, f"{index:02}") for index in range(1, 14)],
    )
    reply = service.handle(message(1, f"/turmas {CODE}")).reply
    buttons = []
    while True:
        rows = reply.markup["inline_keyboard"]
        buttons.extend(row[0] for row in rows if row[0]["text"].startswith("Monitorar"))
        if rows[-1][0]["text"] != "Mais turmas":
            break
        reply = service.handle(callback(1, rows[-1][0]["callback_data"])).reply
    assert len(buttons) == 13
    for button in buttons[:10]:
        assert "adicionada" in service.handle(callback(1, button["callback_data"])).reply.text
    assert "limite de 10" in service.handle(callback(1, buttons[10]["callback_data"])).reply.text
    assert "já acompanha" in service.handle(callback(1, buttons[0]["callback_data"])).reply.text
