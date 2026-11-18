import logging
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

import uerj_monitor.cli as cli
from uerj_monitor.auth_forms import ENTRY_URL, AuthFormError
from uerj_monitor.bot import BotService
from uerj_monitor.bot_monitor import SubscriptionMonitor
from uerj_monitor.catalog import CentralPortalCatalog
from uerj_monitor.discipline_parser import PORTAL_URL
from uerj_monitor.models import Watch
from uerj_monitor.monitor import Monitor
from uerj_monitor.notifier import TelegramClient, TelegramCredentials
from uerj_monitor.outbox import Outbox
from uerj_monitor.portal_client import PortalQueryError, SessionExpired
from uerj_monitor.portal_session import (
    AuthenticatedPortalSession,
    LoginRejected,
    PortalCredentials,
    SessionState,
    load_portal_credentials,
)
from uerj_monitor.storage import SQLiteStore
from uerj_monitor.subscriptions import SubscriptionRepository

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
        self.classes_html = CLASSES

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
        return httpx.Response(200, text=self.classes_html)


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


def test_central_catalog_shares_one_login_and_maps_real_structure_for_invitees(captured_at):
    server = PortalServer()
    with session(server) as portal:
        portal.login()
        catalog = CentralPortalCatalog(portal)
        assert [item.code for item in catalog.search("disciplina DE teste", 1)] == [
            "IME01-00000",
            "IME03-00000",
        ]
        assert [item.code for item in catalog.search("IME01", 2)] == ["IME01-00000"]
        classes = catalog.classes("IME01-00000", 2)
        assert [watch.class_number for watch in classes] == ["1", "2", "3", "4"]
        assert all(watch.discipline_name == "Disciplina de teste 1" for watch in classes)
        assert catalog.classes("INEXISTENTE", 2) == []
        assert catalog.classes("IME02-00000", 2) == []
        assert portal.state == SessionState.READY
        assert catalog.observe("IME01-00000", 1, captured_at)[0].captured_at == captured_at
        assert server.logins == 1


def test_central_shared_poll_preserves_independent_states_destinations_and_failures(captured_at):
    server = PortalServer()
    credentials = TelegramCredentials("123456789:synthetic_test_token")
    moment = captured_at
    with session(server) as portal, SQLiteStore(":memory:") as store:
        portal.login()
        catalog = CentralPortalCatalog(portal)
        repository = SubscriptionRepository(store)
        watch = catalog.classes("IME01-00000", 1)[0]
        for user_id in (1, 2):
            repository.grant(user_id, 1)
            repository.subscribe(user_id, watch)
        monitor = SubscriptionMonitor(store, catalog, credentials, clock=lambda: moment)
        before = len(server.requests)
        result = monitor.poll_once()
        # Um POST de lista + um POST de turmas para as duas pessoas.
        assert len(server.requests) - before == 2
        assert result.observed == 2
        assert result.events == ()
        baselines = [
            store.load_latest(watch, subscription_id=sub.id) for sub in repository.list_active()
        ]
        server.classes_html = CLASSES.replace("<td>25</td><td>25</td>", "<td>25</td><td>24</td>", 1)
        moment += timedelta(minutes=1)
        assert len(monitor.poll_once().events) == 2
        queue = Outbox(store)
        first = queue.claim(replace(credentials, chat_id="1").destination, 1000)
        second = queue.claim(replace(credentials, chat_id="2").destination, 1000)
        assert first.event.id != second.event.id
        latest = [
            store.load_latest(watch, subscription_id=sub.id) for sub in repository.list_active()
        ]
        assert latest != baselines
        moment += timedelta(minutes=1)
        server.expire_classes = True
        before = len(server.requests)
        result = monitor.poll_once()
        assert len(server.requests) - before == 2
        assert result.observed == 0
        assert len(result.failures) == 2
        assert result.events == ()
        assert [
            store.load_latest(watch, subscription_id=sub.id) for sub in repository.list_active()
        ] == latest
        assert len(store.list_events()) == 2
        assert server.logins == 1


@pytest.mark.parametrize("use_env_file", [True, False])
def test_central_bot_cli_uses_only_owner_credentials_and_closes_resources(
    tmp_path, monkeypatch, capsys, use_env_file
):
    token = "123456789:synthetic_test_token"
    monkeypatch.setenv("BOT_TOKEN", token)
    config = tmp_path / "config.toml"
    config.write_text(
        '[monitor]\ndatabase = "bot.sqlite3"\n[bot]\nadmin_user_ids = [1]\n'
        "[portal]\nenabled = true\nrecover_once = true\n"
        + ('env_file = ".env"\n' if use_env_file else ""),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        f"UERJ_MATRICULA={CREDENTIALS.registration}\nUERJ_SENHA={CREDENTIALS.password}\n",
        encoding="utf-8",
    )
    prompts = []
    inputs = iter([CREDENTIALS.registration, CREDENTIALS.password])
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)

    def hidden(prompt):
        prompts.append(prompt)
        return next(inputs)

    monkeypatch.setattr(cli.getpass, "getpass", hidden)
    server, portals, clients, sent = PortalServer(), [], [], []

    def portal_factory(credentials, **kwargs):
        assert credentials == CREDENTIALS
        assert kwargs["recover_once"] is True
        instance = AuthenticatedPortalSession(
            credentials, transport=httpx.MockTransport(server.handle), **kwargs
        )
        portals.append(instance)
        return instance

    def telegram_handler(request):
        import json

        payload = json.loads(request.content)
        if request.url.path.endswith("getUpdates"):
            result = [
                {
                    "update_id": index,
                    "message": {
                        "from": {"id": user_id},
                        "chat": {"id": user_id, "type": "private"},
                        "text": text,
                    },
                }
                for index, (user_id, text) in enumerate(
                    [(1, "/autorizar 2"), (2, "/buscar IME01")], start=1
                )
            ]
        else:
            sent.append(payload)
            result = {"message_id": len(sent)}
        return httpx.Response(200, json={"ok": True, "result": result})

    def telegram_factory(credentials):
        instance = TelegramClient(credentials, transport=httpx.MockTransport(telegram_handler))
        clients.append(instance)
        return instance

    monkeypatch.setattr(cli, "AuthenticatedPortalSession", portal_factory)
    monkeypatch.setattr(cli, "TelegramClient", telegram_factory)
    assert cli.main(["bot", "--config", str(config), "--once"]) == 0
    assert len(prompts) == (0 if use_env_file else 2)
    assert all("oculta" in prompt for prompt in prompts)
    assert len(portals) == server.logins == 1
    assert portals[0].state == SessionState.CLOSED
    assert clients[0]._client.is_closed
    assert [item["chat_id"] for item in sent] == ["1", "2"]
    assert "IME01-00000" in sent[-1]["reply_markup"]["inline_keyboard"][0][0]["text"]
    output = capsys.readouterr()
    assert CREDENTIALS.password not in output.out + output.err
    assert CREDENTIALS.registration not in output.out + output.err
    assert token not in output.out + output.err


def test_central_cli_rejects_failed_login_before_telegram_or_demo_access(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123456789:synthetic_test_token")
    config = tmp_path / "config.toml"
    config.write_text('[portal]\nenabled = true\nenv_file = ".env"\n', encoding="utf-8")
    (tmp_path / ".env").write_text(
        f"UERJ_MATRICULA={CREDENTIALS.registration}\nUERJ_SENHA={CREDENTIALS.password}\n",
        encoding="utf-8",
    )
    server, portals = PortalServer(), []
    server.reject = True

    def portal_factory(credentials, **kwargs):
        instance = AuthenticatedPortalSession(
            credentials, transport=httpx.MockTransport(server.handle), **kwargs
        )
        portals.append(instance)
        return instance

    def no_telegram(*args, **kwargs):
        pytest.fail("Telegram não deve iniciar sem validar a sessão UERJ.")

    monkeypatch.setattr(cli, "AuthenticatedPortalSession", portal_factory)
    monkeypatch.setattr(cli, "TelegramClient", no_telegram)
    assert cli.main(["bot", "--config", str(config), "--once"]) == 1
    assert portals[0].state == SessionState.CLOSED
    assert server.logins == 1
    assert cli.main(["bot-demo", "--config", str(config)]) == 1
    assert len(portals) == 1


def test_central_bot_denies_uninvited_users_before_any_portal_query():
    server = PortalServer()
    with session(server) as portal, SQLiteStore(":memory:") as store:
        portal.login()
        service = BotService(
            SubscriptionRepository(store), CentralPortalCatalog(portal), admin_user_ids=(1,)
        )
        before = len(server.requests)

        def message(user_id, text):
            return {
                "message": {
                    "from": {"id": user_id},
                    "chat": {"id": user_id, "type": "private"},
                    "text": text,
                }
            }

        assert "precisa autorizar" in service.handle(message(2, "/buscar")).reply.text
        assert len(server.requests) == before
        assert "Demonstração" not in service.handle(message(1, "/start")).reply.text
        service.handle(message(1, "/autorizar 2"))
        search = service.handle(message(2, "/buscar teste")).reply
        assert "Demonstração" not in search.text
        assert len(search.markup["inline_keyboard"]) == 2
        assert server.logins == 1
