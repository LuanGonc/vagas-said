import json
from pathlib import Path

import httpx
import pytest

import uerj_monitor.cli as cli
from uerj_monitor.notifier import TelegramClient
from uerj_monitor.outbox import DeliveryWorker

TOKEN = "123456789:synthetic_test_token"
FIXTURE = Path(__file__).parent / "fixtures" / "turmas_sintetico.html"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", TOKEN)
    monkeypatch.setenv("CHAT_ID", "42")
    html = tmp_path / "classes.html"
    html.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text(
        '[monitor]\ndatabase = "state.sqlite3"\n'
        "[telegram]\nenabled = true\n"
        '[[disciplines]]\ncode = "TEST001"\nhtml = "classes.html"\n'
        '[[watches]]\ndiscipline_code = "TEST001"\nclass_number = "01"\n',
        encoding="utf-8",
    )
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("getUpdates"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": [
                        {"message": {"text": "privado", "chat": {"id": 42, "type": "private"}}}
                    ],
                },
            )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

    monkeypatch.setattr(
        cli,
        "TelegramClient",
        lambda credentials: TelegramClient(credentials, transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(
        cli,
        "DeliveryWorker",
        lambda outbox, client: DeliveryWorker(outbox, client, wait=lambda seconds: None),
    )
    return config, html, requests


def test_monitor_with_telegram_queues_and_sends_only_new_events(setup, capsys):
    config, html, requests = setup
    command = ["monitor", "--config", str(config), "--once"]
    assert cli.main(command) == 0
    assert json.loads(capsys.readouterr().out)["notifications"] == {"sent": 0, "errors": 0}
    assert requests == []
    html.write_text(
        html.read_text(encoding="utf-8").replace("<td>18</td>", "<td>3</td>"), encoding="utf-8"
    )
    assert cli.main(command) == 0
    assert json.loads(capsys.readouterr().out)["notifications"]["sent"] == 1
    assert cli.main(command) == 0
    assert json.loads(capsys.readouterr().out)["notifications"]["sent"] == 0
    assert len(requests) == 1
    assert cli.main(["telegram", "queue", "--config", str(config)]) == 0
    queue = json.loads(capsys.readouterr().out)
    assert queue[0]["status"] == "sent"
    assert TOKEN not in str(queue)


def test_explicit_chat_discovery_and_send_test(setup, capsys):
    _, _, requests = setup
    assert cli.main(["telegram", "chats"]) == 0
    assert json.loads(capsys.readouterr().out) == [{"chat_id": 42, "type": "private"}]
    assert cli.main(["telegram", "test"]) == 0
    assert json.loads(capsys.readouterr().out) == {"sent": True, "message_id": 9}
    assert len(requests) == 2


def test_queue_can_be_inspected_without_credentials(setup, monkeypatch, capsys):
    config, _, requests = setup
    monkeypatch.delenv("BOT_TOKEN")
    monkeypatch.delenv("CHAT_ID")
    assert cli.main(["telegram", "queue", "--config", str(config)]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert requests == []


def test_missing_credentials_are_a_controlled_error(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("CHAT_ID", raising=False)
    assert cli.main(["telegram", "test", "--env-file", str(tmp_path / ".env")]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "BOT_TOKEN" in output.err
    assert "Traceback" not in output.err
