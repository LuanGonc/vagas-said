import logging
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from uerj_monitor.auth_forms import ENTRY_URL, AuthFormError
from uerj_monitor.discipline_parser import PORTAL_URL
from uerj_monitor.models import Watch
from uerj_monitor.monitor import Monitor
from uerj_monitor.portal_client import PortalQueryError, SessionExpired
from uerj_monitor.portal_session import (
    AuthenticatedPortalSession,
    LoginRejected,
    PortalCredentials,
    SessionState,
    load_portal_credentials,
)
from uerj_monitor.storage import SQLiteStore

ROOT = Path(__file__).parent / "fixtures"
LOGIN = (ROOT / "login_publico_sanitizado.html").read_text(encoding="utf-8")
HOME = (ROOT / "home_autenticada_sintetica.html").read_text(encoding="utf-8")
LIST = (ROOT / "disciplinas_real_sanitizado.html").read_text(encoding="utf-8")
CLASSES = (ROOT / "turmas_real_sanitizado.html").read_text(encoding="utf-8")
CREDENTIALS = PortalCredentials("000000000000", "test-password-private")


class PortalServer:
    def __init__(self, credentials=CREDENTIALS):
        self.credentials = credentials
        self.requests = []
        self.entries = 0
        self.logins = 0
        self.expire_list = False
        self.expire_classes = False
        self.reject = False
        self.extra_challenge = False
        self.malformed_home = False

    def handle(self, request):
        payload = parse_qs(request.content.decode(), keep_blank_values=True)
        self.requests.append((request, payload))
        if request.method == "GET" and str(request.url) == ENTRY_URL:
            self.entries += 1
            return httpx.Response(
                200,
                text="<script>entry</script>",
                headers={"set-cookie": f"PHPSESSID=private-cookie-{self.entries}; Path=/; Secure"},
            )
        assert str(request.url) == PORTAL_URL
        assert f"PHPSESSID=private-cookie-{self.entries}" in request.headers["cookie"]
        if request.method == "GET":
            assert request.headers["referer"] == ENTRY_URL
            html = LOGIN.replace("fixture-login-token", f"fresh-token-{self.entries}")
            if self.extra_challenge:
                html = html.replace("</form>", '<input name="otp"></form>')
            return httpx.Response(200, text=html)
        if "senha" in payload:
            self.logins += 1
            assert payload["_token"] == [f"fresh-token-{self.entries}"]
            assert payload["matricula"] == [self.credentials.registration]
            assert payload["senha"] == [self.credentials.password]
            return httpx.Response(
                200,
                text=LOGIN if self.reject else ("<p>unknown</p>" if self.malformed_home else HOME),
            )
        if payload["requisicao"] == ["fixture-list-action"]:
            if self.expire_list:
                self.expire_list = False
                return httpx.Response(200, text=LOGIN)
            assert payload["_token"] in (["fixture-home-token"], ["fixture-token"])
            assert payload["flg_logado"] == ["fixture-logged-field"]
            return httpx.Response(
                200, text=LIST.replace("000000000000", self.credentials.registration)
            )
        assert payload["requisicao"] == ["fixture-action"]
        assert payload["_token"] == ["fixture-token"]
        assert payload["matricula"] == [self.credentials.registration]
        if self.expire_classes:
            self.expire_classes = False
            return httpx.Response(200, text=LOGIN)
        return httpx.Response(200, text=CLASSES)


def session(server, **kwargs):
    return AuthenticatedPortalSession(
        CREDENTIALS, transport=httpx.MockTransport(server.handle), **kwargs
    )


def test_login_bootstraps_cookie_then_validates_list_before_ready(caplog):
    server = PortalServer()
    caplog.set_level(logging.DEBUG)
    with session(server) as portal:
        assert portal.state == SessionState.DISCONNECTED
        portal.login()
        assert portal.state == SessionState.READY
        assert len(portal.disciplines) == 3
        assert "TURMA:" in portal.fetch_discipline_classes("IME01-00000")
        logging.getLogger("httpcore.http11").debug("Set-Cookie: private-cookie-1")
    assert portal.state == SessionState.CLOSED
    assert not portal._http.cookies
    assert portal._credentials is None
    assert server.logins == 1
    assert "private-cookie-1" not in caplog.text
    assert CREDENTIALS.password not in caplog.text
    assert CREDENTIALS.password not in repr(CREDENTIALS)


def test_rejected_password_is_never_retried_even_with_recovery_enabled():
    server = PortalServer()
    server.reject = True
    with session(server, recover_once=True) as portal:
        with pytest.raises(LoginRejected):
            portal.login()
        assert portal.state == SessionState.FAILED
        with pytest.raises(PortalQueryError):
            portal.fetch_discipline_classes("IME01-00000")
    assert server.logins == 1


def test_additional_challenge_stops_before_submitting_a_password():
    server = PortalServer()
    server.extra_challenge = True
    with session(server) as portal:
        with pytest.raises(AuthFormError):
            portal.login()
    assert server.logins == 0


@pytest.mark.parametrize("stage", ["expire_list", "expire_classes"])
def test_expiration_is_detected_with_http_200_and_does_not_become_empty_data(stage):
    server = PortalServer()
    with session(server) as portal:
        portal.login()
        setattr(server, stage, True)
        with pytest.raises(SessionExpired):
            portal.fetch_discipline_classes("IME01-00000")
        assert portal.state == SessionState.EXPIRED
        count = len(server.requests)
        with pytest.raises(SessionExpired):
            portal.fetch_discipline_classes("IME01-00000")
        assert len(server.requests) == count
    assert server.logins == 1


def test_recovery_is_opt_in_uses_a_fresh_login_and_is_limited_to_once():
    server = PortalServer()
    with session(server, recover_once=True) as portal:
        portal.login()
        server.expire_classes = True
        assert "TURMA:" in portal.fetch_discipline_classes("IME01-00000")
        assert server.logins == 2
        assert server.entries == 2
        server.expire_list = True
        with pytest.raises(SessionExpired):
            portal.refresh_disciplines()
        assert server.logins == 2


def test_unknown_home_is_not_a_confirmed_login():
    server = PortalServer()
    server.malformed_home = True
    with session(server) as portal:
        with pytest.raises(AuthFormError):
            portal.login()
        assert portal.state == SessionState.FAILED


def test_expiration_preserves_monitor_baseline_and_emits_no_vacancy_alert():
    server = PortalServer()
    with session(server) as portal, SQLiteStore(":memory:") as store:
        portal.login()
        watch = Watch("IME01-00000", "1")
        monitor = Monitor(portal, store, (watch,))
        assert monitor.poll_once().observed == 1
        baseline = store.load_latest(watch)
        server.expire_classes = True
        result = monitor.poll_once()
        assert result.observed == 0
        assert result.events == ()
        assert result.failures[0].reason == "source_error"
        assert store.load_latest(watch) == baseline
        assert store.list_events() == []


def test_local_env_file_is_explicit_and_password_whitespace_is_preserved(tmp_path):
    path = tmp_path / ".env"
    path.write_text('UERJ_MATRICULA=000000000000\nUERJ_SENHA=" secret "\n', encoding="utf-8-sig")
    credentials = load_portal_credentials(path)
    assert credentials.password == " secret "
    assert credentials.registration == "000000000000"


def test_sessions_for_two_accounts_do_not_share_credentials_or_cookie_lifecycle():
    second_credentials = PortalCredentials("000000000001", "second-private-password")
    first_server, second_server = PortalServer(), PortalServer(second_credentials)
    with (
        session(first_server) as first,
        AuthenticatedPortalSession(
            second_credentials, transport=httpx.MockTransport(second_server.handle)
        ) as second,
    ):
        first.login()
        second.login()
        assert first._http.cookies is not second._http.cookies
        first.close()
        assert second.state == SessionState.READY
        assert second._http.cookies
        assert "TURMA:" in second.fetch_discipline_classes("IME01-00000")
        assert first_server.logins == second_server.logins == 1
