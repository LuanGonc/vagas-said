import json
import logging
from dataclasses import replace

import httpx
import pytest

from uerj_monitor.config import ConfigError
from uerj_monitor.models import ClassObservation, VacancyNumbers, Watch
from uerj_monitor.notifier import (
    TelegramClient,
    TelegramCredentials,
    TelegramError,
    format_event,
    load_credentials,
)
from uerj_monitor.storage import EventKind, StoredEvent

TOKEN = "123456789:synthetic_test_token"
CREDENTIALS = TelegramCredentials(TOKEN, "42")


def test_send_uses_plain_text_and_redacts_token_even_with_debug_logging(caplog):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 12}})

    caplog.set_level(logging.DEBUG)
    with TelegramClient(CREDENTIALS, transport=httpx.MockTransport(handler)) as client:
        assert client.send_message("Disciplina <A> & turma 01") == 12
        logging.getLogger("httpcore.http11").debug("request %s", TOKEN)
    assert requests[0]["chat_id"] == "42"
    assert requests[0]["text"] == "Disciplina <A> & turma 01"
    assert "parse_mode" not in requests[0]
    assert TOKEN not in caplog.text
    assert "redacted" in caplog.text
    assert TOKEN not in repr(CREDENTIALS)


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (400, "bad_request", False),
        (401, "unauthorized", False),
        (403, "forbidden", False),
        (409, "conflict", False),
        (500, "server_error", True),
        (429, "rate_limited", True),
    ],
)
def test_api_errors_are_sanitized_and_classified(status, code, retryable):
    def handler(request):
        return httpx.Response(
            status,
            json={
                "ok": False,
                "error_code": status,
                "description": f"privado {TOKEN}",
                "parameters": {"retry_after": 1200},
            },
        )

    with TelegramClient(CREDENTIALS, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TelegramError) as raised:
            client.send_message("teste")
    assert raised.value.code == code
    assert raised.value.retryable is retryable
    assert raised.value.uncertain is False
    assert TOKEN not in str(raised.value)
    assert "privado" not in str(raised.value)
    if status == 429:
        assert raised.value.retry_after == 1200


@pytest.mark.parametrize(
    "exception,retryable,uncertain",
    [
        (httpx.ConnectTimeout, True, False),
        (httpx.ConnectError, True, False),
        (httpx.ReadTimeout, False, True),
        (httpx.WriteError, False, True),
    ],
)
def test_transport_errors_do_not_retry_inside_client(exception, retryable, uncertain):
    calls = []

    def handler(request):
        calls.append(request)
        raise exception(f"URL sensível: {request.url}")

    with TelegramClient(CREDENTIALS, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TelegramError) as raised:
            client.send_message("teste")
    assert len(calls) == 1
    assert raised.value.retryable is retryable
    assert raised.value.uncertain is uncertain
    assert TOKEN not in str(raised.value)
    assert raised.value.__suppress_context__ is True


@pytest.mark.parametrize(
    "status,body",
    [(502, "not json"), (200, "[]"), (200, '{"ok": true}'), (200, '{"ok": true, "result": {}}')],
)
def test_invalid_confirmation_is_uncertain(status, body):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text=body))
    with TelegramClient(CREDENTIALS, transport=transport) as client:
        with pytest.raises(TelegramError) as raised:
            client.send_message("teste")
    assert raised.value.uncertain is True


def test_redirects_are_not_followed():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://example.invalid/private"})

    with TelegramClient(CREDENTIALS, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TelegramError, match="delivery_uncertain"):
            client.send_message("teste")
    assert len(requests) == 1


def test_chat_discovery_returns_only_ids_and_types():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {
                        "message": {
                            "text": "conteúdo privado",
                            "chat": {"id": 42, "type": "private", "first_name": "Nome privado"},
                        }
                    },
                    {"message": {"chat": {"id": 42, "type": "private"}}},
                    {"callback_query": {"data": "ignorar"}},
                ],
            },
        )

    with TelegramClient(CREDENTIALS, transport=httpx.MockTransport(handler)) as client:
        assert client.discover_chats() == [{"chat_id": 42, "type": "private"}]
    assert requests == [{"timeout": 0, "limit": 100}]


@pytest.mark.parametrize("text", ["", " ", "x" * 4097, "😀" * 2049])
def test_invalid_message_is_rejected_before_network(text):
    def unexpected(request):
        pytest.fail("Mensagem inválida não deve acessar a rede.")

    with TelegramClient(CREDENTIALS, transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(ValueError):
            client.send_message(text)


def test_credentials_support_bom_and_environment_precedence(tmp_path, monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("CHAT_ID", raising=False)
    path = tmp_path / ".env"
    path.write_text(f"BOT_TOKEN={TOKEN}\nCHAT_ID=42\n", encoding="utf-8-sig")
    assert load_credentials(path) == CREDENTIALS
    monkeypatch.setenv("CHAT_ID", "-123")
    assert load_credentials(path).chat_id == "-123"


def test_chat_id_can_be_omitted_only_for_discovery(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", TOKEN)
    monkeypatch.delenv("CHAT_ID", raising=False)
    path = tmp_path / ".env"
    assert load_credentials(path, require_chat=False).chat_id == ""
    with pytest.raises(ConfigError):
        load_credentials(path)


def test_invalid_credentials_do_not_echo_values():
    with pytest.raises(ConfigError) as raised:
        TelegramCredentials("senha_privada")
    assert "senha_privada" not in str(raised.value)


def test_destination_survives_token_rotation_but_not_chat_change():
    assert CREDENTIALS.destination == TelegramCredentials("123456789:new_token", "42").destination
    assert CREDENTIALS.destination != TelegramCredentials(TOKEN, "43").destination


def test_message_reports_counts_without_claiming_an_available_seat(captured_at):
    before = ClassObservation(
        Watch("TEST001", "01"), captured_at, VacancyNumbers(25, 25, 30, 18, 10)
    )
    after = replace(before, numbers=replace(before.numbers, requested_total=3))
    text = format_event(StoredEvent(1, EventKind.NUMBERS_CHANGED, before, after))
    assert "Monitor de Vagas UERJ" in text
    assert "Solicitadas / Total: 18 → 3" in text
    assert "vaga disponível" not in text.lower()
    assert "Contagens não confirmam matrícula" in text
    assert "TEST001" in text
    assert "15:00:00 -0300 (Brasília)" in text
