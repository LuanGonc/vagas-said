import getpass
import json
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest

import uerj_monitor.cli as cli
from uerj_monitor.portal_session import LoginRejected, SessionState

ROOT = Path(__file__).parent / "fixtures"
PASSWORD = "local-private-password"


@pytest.fixture
def fake_portal(monkeypatch):
    instances = []

    class Portal:
        def __init__(self, credentials, recover_once=False):
            self.credentials = credentials
            self.recover_once = recover_once
            self.closed = False
            self.reject = False
            instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def login(self):
            if self.reject:
                raise LoginRejected("Login não confirmado.")
            self.state = SessionState.READY
            self.disciplines = [
                SimpleNamespace(has_classes=True),
                SimpleNamespace(has_classes=False),
            ]

        def fetch_discipline_classes(self, code):
            assert code == "IME01-00000"
            return (ROOT / "turmas_real_sanitizado.html").read_text(encoding="utf-8")

    monkeypatch.setattr(cli, "AuthenticatedPortalSession", Portal)
    return instances


def env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text(f"UERJ_MATRICULA=000000000000\nUERJ_SENHA={PASSWORD}\n", encoding="utf-8")
    return path


def test_env_mode_is_explicit_and_prints_only_summary(tmp_path, fake_portal, capsys):
    code = cli.main(
        [
            "portal",
            "check",
            "--env-file",
            str(env_file(tmp_path)),
            "--discipline-code",
            "IME01-00000",
            "--recover-once",
        ]
    )
    assert code == 0
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["authenticated"] is True
    assert result["disciplines"] == 2
    assert result["class_numbers"] == ["1", "2", "3", "4"]
    assert PASSWORD not in output.out + output.err
    assert "000000000000" not in output.out + output.err
    assert fake_portal[0].closed
    assert fake_portal[0].recover_once is True


def test_interactive_mode_uses_hidden_prompts(monkeypatch, fake_portal, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    values = iter(["000000000000", PASSWORD])
    prompts = []

    def hidden(prompt):
        prompts.append(prompt)
        return next(values)

    monkeypatch.setattr(cli.getpass, "getpass", hidden)
    assert cli.main(["portal", "check"]) == 0
    assert len(prompts) == 2
    assert all("oculta" in prompt for prompt in prompts)
    assert fake_portal[0].credentials.password == PASSWORD
    assert fake_portal[0].recover_once is False
    assert PASSWORD not in capsys.readouterr().out


def test_noninteractive_mode_does_not_read_stdin_or_try_authentication(
    monkeypatch, fake_portal, capsys
):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.main(["portal", "check"]) == 1
    assert fake_portal == []
    assert "terminal interativo" in capsys.readouterr().err


def test_no_visible_password_fallback_is_allowed(monkeypatch, fake_portal, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)

    def unsupported(prompt):
        warnings.warn("unsupported terminal", getpass.GetPassWarning, stacklevel=2)

    monkeypatch.setattr(cli.getpass, "getpass", unsupported)
    assert cli.main(["portal", "check"]) == 1
    assert fake_portal == []
    assert "entrada oculta" in capsys.readouterr().err


def test_bad_credentials_file_does_not_attempt_login(tmp_path, fake_portal, capsys):
    path = tmp_path / ".env"
    path.write_text("UERJ_MATRICULA=private-invalid\nUERJ_SENHA=private\n", encoding="utf-8")
    assert cli.main(["portal", "check", "--env-file", str(path)]) == 1
    assert fake_portal == []
    output = capsys.readouterr()
    assert "private-invalid" not in output.err


def test_unexpected_library_error_is_not_echoed(tmp_path, monkeypatch, capsys):
    def failing(*args, **kwargs):
        raise ValueError(f"sensitive library error {PASSWORD}")

    monkeypatch.setattr(cli, "AuthenticatedPortalSession", failing)
    assert cli.main(["portal", "check", "--env-file", str(env_file(tmp_path))]) == 1
    output = capsys.readouterr()
    assert PASSWORD not in output.err
    assert "sensitive library" not in output.err
