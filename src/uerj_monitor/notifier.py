"""Telegram Bot API com erros sanitizados e sem repetição automática no transporte."""

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import dotenv_values

from uerj_monitor.config import ConfigError
from uerj_monitor.storage import StoredEvent


@dataclass(frozen=True)
class TelegramCredentials:
    token: str = field(repr=False)
    chat_id: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", self.token):
            raise ConfigError("BOT_TOKEN ausente ou inválido; preencha o .env local.")
        if self.chat_id and not re.fullmatch(r"-?[0-9]+", self.chat_id):
            raise ConfigError("CHAT_ID deve ser um identificador numérico do Telegram.")

    @property
    def destination(self) -> str:
        # Identifica bot/chat sem gravar token ou CHAT_ID na fila.
        return hashlib.sha256(f"{self.token.split(':', 1)[0]}:{self.chat_id}".encode()).hexdigest()


def load_credentials(
    env_file: Path, *, require_chat: bool = True, ignore_chat: bool = False
) -> TelegramCredentials:
    try:
        values = (
            dotenv_values(env_file, interpolate=False, encoding="utf-8-sig")
            if env_file.exists()
            else {}
        )
    except (OSError, UnicodeError) as error:
        raise ConfigError("Não foi possível ler o arquivo .env local.") from error
    token = os.environ.get("BOT_TOKEN", values.get("BOT_TOKEN") or "").strip()
    chat = os.environ.get("CHAT_ID", values.get("CHAT_ID") or "").strip()
    if ignore_chat:
        chat = ""
    credentials = TelegramCredentials(token, chat)
    if require_chat and not credentials.chat_id:
        raise ConfigError("CHAT_ID ausente; execute telegram chats e preencha o .env local.")
    return credentials


class TelegramError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        uncertain: bool = False,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(f"Falha no Telegram: {code}.")
        self.code = code
        self.retryable = retryable
        self.uncertain = uncertain
        self.retry_after = retry_after


class _TokenFilter(logging.Filter):
    def __init__(self, token: str) -> None:
        super().__init__()
        self.token = token

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = record.getMessage().replace(self.token, "<redacted>")
        record.args = ()
        return True


class TelegramClient:
    def __init__(
        self,
        credentials: TelegramCredentials,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.credentials = credentials
        self._filter = _TokenFilter(credentials.token)
        # httpx registra a URL em INFO; ela contém o token na Bot API.
        self._loggers = [
            logging.getLogger(name)
            for name in (
                "httpx",
                "httpcore",
                "httpcore.connection",
                "httpcore.http11",
                "httpcore.http2",
                "httpcore.proxy",
                "httpcore.socks",
            )
        ]
        for logger in self._loggers:
            logger.addFilter(self._filter)
        self._client = httpx.Client(
            timeout=httpx.Timeout(10, connect=5),
            follow_redirects=False,
            transport=transport,
        )

    def __enter__(self) -> "TelegramClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()
        for logger in self._loggers:
            logger.removeFilter(self._filter)

    def _request(self, method: str, payload: dict) -> object:
        try:
            response = self._client.post(
                f"https://api.telegram.org/bot{self.credentials.token}/{method}", json=payload
            )
        except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout):
            raise TelegramError("connection_error", retryable=True) from None
        except httpx.HTTPError:
            # A requisição pode ter sido entregue antes de perdermos a resposta.
            raise TelegramError("delivery_uncertain", uncertain=True) from None
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict) and body.get("ok") is False:
            code = body.get("error_code", response.status_code)
            if type(code) is not int:
                code = response.status_code
            parameters = body.get("parameters")
            retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
            if type(retry_after) is not int or retry_after < 1:
                retry_after = None
            if code == 429:
                raise TelegramError("rate_limited", retryable=True, retry_after=retry_after)
            if type(code) is int and code >= 500:
                raise TelegramError("server_error", retryable=True)
            safe_code = {
                400: "bad_request",
                401: "unauthorized",
                403: "forbidden",
                404: "not_found",
                409: "conflict",
            }.get(code, "rejected")
            raise TelegramError(safe_code)
        if not 200 <= response.status_code < 300 or not isinstance(body, dict):
            raise TelegramError("delivery_uncertain", uncertain=True)
        if body.get("ok") is not True or "result" not in body:
            raise TelegramError("delivery_uncertain", uncertain=True)
        return body["result"]

    def send_message(
        self, text: str, *, chat_id: str | None = None, reply_markup: dict | None = None
    ) -> int:
        destination = chat_id if chat_id is not None else self.credentials.chat_id
        if not destination or not re.fullmatch(r"-?[0-9]+", destination):
            raise ConfigError("CHAT_ID é obrigatório para enviar mensagens.")
        if not text.strip() or len(text.encode("utf-16-le")) // 2 > 4096:
            raise ValueError("A mensagem precisa ter de 1 a 4096 unidades UTF-16.")
        payload = {
            "chat_id": destination,
            "text": text,
            "link_preview_options": {"is_disabled": True},
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = self._request("sendMessage", payload)
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if type(message_id) is not int:
            raise TelegramError("delivery_uncertain", uncertain=True)
        return message_id

    def get_updates(self, offset: int | None = None, timeout: int = 5) -> list[dict]:
        payload = {
            "timeout": timeout,
            "limit": 100,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self._request("getUpdates", payload)
        if not isinstance(result, list):
            raise TelegramError("invalid_response")
        return result

    def answer_callback(self, callback_id: str) -> None:
        self._request("answerCallbackQuery", {"callback_query_id": callback_id})

    def for_chat(self, chat_id: int) -> "TelegramRecipient":
        return TelegramRecipient(self, replace(self.credentials, chat_id=str(chat_id)))

    def discover_chats(self) -> list[dict]:
        # Sem offset: não confirma/apaga atualizações recebidas por outro consumidor.
        result = self._request("getUpdates", {"timeout": 0, "limit": 100})
        if not isinstance(result, list):
            raise TelegramError("invalid_response")
        chats = {}
        for update in result:
            message = update.get("message") if isinstance(update, dict) else None
            chat = message.get("chat") if isinstance(message, dict) else None
            if isinstance(chat, dict) and type(chat.get("id")) is int:
                if chat.get("type") in {"private", "group", "supergroup"}:
                    chats[chat["id"]] = {"chat_id": chat["id"], "type": chat["type"]}
        return list(chats.values())


@dataclass
class TelegramRecipient:
    client: TelegramClient = field(repr=False)
    credentials: TelegramCredentials = field(repr=False)

    def send_message(self, text: str) -> int:
        return self.client.send_message(text, chat_id=self.credentials.chat_id)


def _label(value: str) -> str:
    return " ".join(value.split())[:100]


def format_event(event: StoredEvent) -> str:
    observation = event.current
    watch = observation.watch
    labels = (
        "Oferecidas (atualizadas)",
        "Ocupadas",
        "Oferecidas (solicitação)",
        "Solicitadas / Total",
        "Solicitadas / Preferenciais",
    )
    lines = [
        "Demonstração local — Monitor de Vagas UERJ",
        "Alteração nas contagens observada.",
        f"Disciplina: {_label(watch.discipline_code)}",
    ]
    if watch.discipline_name:
        lines.append(f"Nome: {_label(watch.discipline_name)}")
    lines.extend(
        [
            f"Turma: {_label(watch.class_number)} | Tipo: {_label(watch.vacancy_type)}",
            f"Fase informada: {observation.source_phase.value}",
        ]
    )
    for label, before, after in zip(
        labels, event.previous.numbers.as_tuple(), observation.numbers.as_tuple(), strict=True
    ):
        if before is not None and after is not None and before != after:
            lines.append(f"{label}: {before} → {after}")
    lines.extend(
        [
            "Captura: "
            + observation.captured_at.astimezone(ZoneInfo("America/Sao_Paulo")).strftime(
                "%d/%m/%Y %H:%M:%S %z"
            )
            + " (Brasília)",
            "Contagens não confirmam matrícula. Confira no Aluno Online:",
            "https://www.alunoonline.uerj.br/requisicaoaluno/",
        ]
    )
    return "\n".join(lines)
