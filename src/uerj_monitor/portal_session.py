"""Sessão UERJ local e isolada, com recuperação explícita e limitada."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from dotenv import dotenv_values

from uerj_monitor.auth_forms import (
    ENTRY_URL,
    has_login_form,
    parse_list_navigation,
    parse_login_form,
)
from uerj_monitor.discipline_parser import PORTAL_URL, parse_navigation_fields
from uerj_monitor.parser import ParseError
from uerj_monitor.portal_client import ObservedPortalClient, PortalQueryError, SessionExpired


class SessionState(StrEnum):
    DISCONNECTED = "disconnected"
    READY = "ready"
    EXPIRED = "expired"
    FAILED = "failed"
    CLOSED = "closed"


class LoginRejected(PortalQueryError):
    """O servidor não confirmou login; nenhuma repetição automática de senha."""


class PortalCredentialError(ValueError):
    """Erro local de credenciais, com mensagem sanitizada."""


@dataclass(frozen=True)
class PortalCredentials:
    registration: str = field(repr=False)
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not self.registration.isascii()
            or not self.registration.isdecimal()
            or len(self.registration) < 12
        ):
            raise PortalCredentialError(
                "A matrícula deve conter ao menos 12 dígitos, como no formulário público."
            )
        if not self.password:
            raise PortalCredentialError("Preencha a senha localmente.")


def load_portal_credentials(path: Path) -> PortalCredentials:
    if not path.is_file():
        raise PortalCredentialError("Informe um arquivo local de credenciais existente.")
    try:
        values = dotenv_values(path, encoding="utf-8-sig", interpolate=False)
    except (OSError, UnicodeError):
        raise PortalCredentialError(
            "Não foi possível ler o arquivo local de credenciais."
        ) from None
    return PortalCredentials(
        (values.get("UERJ_MATRICULA") or "").strip(), values.get("UERJ_SENHA") or ""
    )


def _html(response: httpx.Response) -> str:
    encoding = (
        response.charset_encoding
        or BeautifulSoup(response.content, "html.parser").original_encoding
    )
    return response.content.decode(encoding or "utf-8", errors="replace")


class AuthenticatedPortalSession:
    def __init__(
        self,
        credentials: PortalCredentials,
        *,
        transport: httpx.BaseTransport | None = None,
        recover_once: bool = False,
    ) -> None:
        self._credentials = credentials
        self._http = httpx.Client(transport=transport, timeout=15, follow_redirects=False)
        self._navigation = None
        self._client = None
        self._recover_once = recover_once
        self._recoveries = 0
        self.state = SessionState.DISCONNECTED
        # O trace de httpcore pode registrar Set-Cookie. Suprimi-lo durante a sessão
        # evita expor credenciais de sessão mesmo com logging DEBUG do aplicativo.
        self._quiet_filter = _QuietHTTPTrace()
        self._trace_loggers = [
            logging.getLogger(name)
            for name in (
                "httpcore.connection",
                "httpcore.http11",
                "httpcore.http2",
                "httpcore.proxy",
                "httpcore.socks",
            )
        ]
        for logger in self._trace_loggers:
            logger.addFilter(self._quiet_filter)

    def __enter__(self) -> "AuthenticatedPortalSession":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client = self._navigation = self._credentials = None
        self._http.cookies.clear()
        try:
            self._http.close()
        finally:
            for logger in self._trace_loggers:
                logger.removeFilter(self._quiet_filter)
            self.state = SessionState.CLOSED

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: dict | None = None,
        referer: str = PORTAL_URL,
        login: bool = False,
    ) -> str:
        try:
            response = self._http.request(method, url, data=data, headers={"Referer": referer})
        except httpx.HTTPError:
            raise PortalQueryError("Falha de rede; a operação não foi repetida.") from None
        if response.status_code != 200:
            raise PortalQueryError("O portal não confirmou a operação HTTP.")
        html = _html(response)
        if not login and has_login_form(html):
            raise SessionExpired("Sessão expirada. Autentique-se novamente no ambiente local.")
        return html

    def login(self) -> None:
        if self.state == SessionState.CLOSED:
            raise PortalQueryError("A sessão já foi encerrada.")
        self._client = self._navigation = None
        self.state = SessionState.DISCONNECTED
        self._http.cookies.clear()
        try:
            self._request("GET", ENTRY_URL, login=True, referer=ENTRY_URL)
            page = self._request("GET", PORTAL_URL, referer=ENTRY_URL, login=True)
            form = parse_login_form(page)
            home = self._request(
                "POST",
                PORTAL_URL,
                login=True,
                data=form.payload(self._credentials.registration, self._credentials.password),
            )
            if has_login_form(home):
                raise LoginRejected(
                    "Login não confirmado. Confira os dados localmente; não houve repetição."
                )
            self._navigation = parse_list_navigation(home)
            self._load_list()
        except (PortalQueryError, ParseError):
            self.state = SessionState.FAILED
            self._client = self._navigation = None
            self._http.cookies.clear()
            raise
        self.state = SessionState.READY
        self._recoveries = 0

    def _load_list(self) -> None:
        html = self._request("POST", PORTAL_URL, data=self._navigation.payload())
        fields = parse_navigation_fields(html)
        self._navigation = replace(self._navigation, token=fields.token)
        self._client = ObservedPortalClient(self._http, html)

    def _operate(self, operation: Callable[[], object]):
        if self.state not in {SessionState.READY, SessionState.EXPIRED}:
            raise PortalQueryError("A sessão não está autenticada. Execute o login local.")
        try:
            if self.state == SessionState.EXPIRED:
                raise SessionExpired("A sessão precisa ser renovada.")
            return operation()
        except SessionExpired:
            self.state = SessionState.EXPIRED
            self._client = self._navigation = None
            self._http.cookies.clear()
            if not self._recover_once or self._recoveries:
                raise
            self.login()
            self._recoveries = 1
            try:
                return operation()
            except SessionExpired:
                self.state = SessionState.EXPIRED
                self._client = self._navigation = None
                self._http.cookies.clear()
                raise
            except (PortalQueryError, ParseError):
                self._invalidate()
                raise
        except (PortalQueryError, ParseError):
            self._invalidate()
            raise

    def _invalidate(self) -> None:
        self.state = SessionState.FAILED
        self._client = self._navigation = None
        self._http.cookies.clear()

    def refresh_disciplines(self):
        def refresh():
            self._load_list()
            return list(self._client.disciplines.values())

        return self._operate(refresh)

    @property
    def disciplines(self):
        if self.state != SessionState.READY or self._client is None:
            raise PortalQueryError("O catálogo exige uma sessão autenticada.")
        return list(self._client.disciplines.values())

    def fetch_discipline_classes(self, discipline_code: str) -> str:
        def fetch():
            self._load_list()
            return self._client.fetch_discipline_classes(discipline_code)

        return self._operate(fetch)


class _QuietHTTPTrace(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return False
