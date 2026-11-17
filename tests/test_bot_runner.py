import json
from dataclasses import replace
from pathlib import Path

import httpx

from uerj_monitor.bot import BotService
from uerj_monitor.bot_monitor import SubscriptionMonitor
from uerj_monitor.bot_runner import BotRunner
from uerj_monitor.catalog import LocalCatalog
from uerj_monitor.config import load_config
from uerj_monitor.notifier import TelegramClient, TelegramCredentials
from uerj_monitor.storage import SQLiteStore
from uerj_monitor.subscriptions import SubscriptionRepository


def test_polling_commands_callbacks_restart_and_private_notification(tmp_path):
    config = load_config(
        Path(__file__).parents[1] / "config.bot.example.toml", require_watches=False
    )
    code = "IME01-06766"
    html = tmp_path / "classes.html"
    html.write_text(config.sources[code].read_text(encoding="utf-8"), encoding="utf-8")
    config = replace(config, sources={code: html})
    updates, requests = [], []
    now = [1000.0]

    def advance(seconds):
        now[0] += seconds

    def message(update_id, user_id, text):
        return {
            "update_id": update_id,
            "message": {
                "from": {"id": user_id},
                "chat": {"id": user_id, "type": "private"},
                "text": text,
            },
        }

    def click(update_id, data):
        return {
            "update_id": update_id,
            "callback_query": {
                "from": {"id": 2},
                "id": f"cb{update_id}",
                "data": data,
                "message": {"chat": {"id": 2, "type": "private"}},
            },
        }

    def handler(request):
        payload = json.loads(request.content)
        requests.append((request.url.path.rsplit("/", 1)[-1], payload))
        if request.url.path.endswith("getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": list(updates)})
        if request.url.path.endswith("answerCallbackQuery"):
            return httpx.Response(200, json={"ok": True, "result": True})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(requests)}})

    credentials = TelegramCredentials("123456789:synthetic_test_token")
    with SQLiteStore(tmp_path / "bot.sqlite3") as store:
        service = BotService(
            SubscriptionRepository(store), LocalCatalog(config), admin_user_ids=(1,)
        )
        with TelegramClient(credentials, transport=httpx.MockTransport(handler)) as client:
            monitor = SubscriptionMonitor(store, service.catalog, credentials)
            runner = BotRunner(
                service,
                monitor,
                client,
                store,
                config.scheduler,
                clock=lambda: now[0],
                wait=advance,
            )
            updates.extend([message(1, 1, "/autorizar 2"), message(2, 2, "/buscar calculo ii")])
            runner.run_once(timeout=0)
            payload = requests[-1][1]
            discipline_button = payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
            updates[:] = [click(3, discipline_button)]
            runner.run_once(timeout=0)
            class_button = requests[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
            updates[:] = [click(4, class_button)]
            runner.run_once(timeout=0)
            assert len(service.repository.list_active(2)) == 1
            assert any(method == "answerCallbackQuery" for method, _ in requests)
            # Nova instância: o update já processado não cria resposta/assinatura novamente.
            before = len([entry for entry in requests if entry[0] == "sendMessage"])
            runner = BotRunner(
                service,
                monitor,
                client,
                store,
                config.scheduler,
                clock=lambda: now[0],
                wait=advance,
            )
            runner.run_once(timeout=0)
            assert len([entry for entry in requests if entry[0] == "sendMessage"]) == before
            assert requests[-1][1]["offset"] == 5
            updates.clear()
            html.write_text(
                html.read_text(encoding="utf-8").replace("<td>18</td>", "<td>3</td>"),
                encoding="utf-8",
            )
            now[0] += 100
            runner.run_once(timeout=0)
            sent = [payload for method, payload in requests if method == "sendMessage"]
            assert sent[-1]["chat_id"] == "2"
            assert "Solicitadas / Total: 18" in sent[-1]["text"]
            assert service.repository.list_active(2)
