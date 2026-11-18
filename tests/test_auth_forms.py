from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from uerj_monitor.auth_forms import (
    AccessBlocked,
    AuthFormError,
    parse_list_navigation,
    parse_login_form,
)
from uerj_monitor.portal_client import is_login_page

ROOT = Path(__file__).parent / "fixtures"


def test_public_login_form_uses_returned_opaque_fields_without_exposing_them():
    html = (ROOT / "login_publico_sanitizado.html").read_text(encoding="utf-8")
    assert is_login_page(html)
    form = parse_login_form(html)
    payload = form.payload("000000000000", "fictional password")
    assert payload == {
        "_token": "fixture-login-token",
        "requisicao": "fixture-login-action",
        "matricula": "000000000000",
        "senha": "fictional password",
    }
    assert "fixture-login-token" not in repr(form)


@pytest.mark.parametrize("damage", ["destination", "method", "duplicate", "challenge"])
def test_login_unknown_or_additional_controls_stop_before_credentials(damage):
    soup = BeautifulSoup(
        (ROOT / "login_publico_sanitizado.html").read_text(encoding="utf-8"), "html.parser"
    )
    if damage == "destination":
        soup.form["action"] = "https://example.invalid/"
    elif damage == "method":
        soup.form["method"] = "get"
    else:
        soup.form.append(
            soup.new_tag("input", attrs={"name": "otp" if damage == "challenge" else "_token"})
        )
    with pytest.raises(AuthFormError):
        parse_login_form(str(soup))


def test_list_navigation_uses_dynamic_fields_and_does_not_treat_logged_field_as_registration():
    nav = parse_list_navigation(
        (ROOT / "home_autenticada_sintetica.html").read_text(encoding="utf-8")
    )
    assert nav.payload() == {
        "flg_logado": "fixture-logged-field",
        "_token": "fixture-home-token",
        "target": "0",
        "hRef": "",
        "requisicao": "fixture-list-action",
    }
    assert "fixture-home-token" not in repr(nav)
    assert "fixture-logged-field" not in repr(nav)


def test_blocked_menu_is_not_overridden():
    html = (
        (ROOT / "home_autenticada_sintetica.html")
        .read_text(encoding="utf-8")
        .replace("executaMenu(0,", "executaMenu(1,")
    )
    with pytest.raises(AccessBlocked):
        parse_list_navigation(html)
