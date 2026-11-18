"""Formulário público observado e navegação descrita pelo JavaScript do portal.

Login: HTML público consultado em 18/11/2026. Menu autenticado: contrato do JS
executaMenu e campos do HAR; o HTML pós-login ainda precisa de validação local real.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from uerj_monitor.discipline_parser import PORTAL_URL
from uerj_monitor.parser import _normalized, _text
from uerj_monitor.portal_client import PortalQueryError, is_login_page

ENTRY_URL = "https://www.alunoonline.uerj.br/"


class AuthFormError(PortalQueryError):
    """Estrutura não reconhecida; não reproduz conteúdo nem valores privados."""


class AccessBlocked(AuthFormError):
    """O portal exige interação extra ou bloqueou a navegação."""


def _safe_form(form: Tag) -> None:
    if form.get("method", "get").casefold() != "post":
        raise AuthFormError("Método do formulário não corresponde ao observado.")
    if urljoin(PORTAL_URL, form.get("action", "")) != PORTAL_URL:
        raise AuthFormError("Destino do formulário não corresponde ao portal.")


def _hidden(form: Tag, name: str) -> str:
    inputs = form.find_all("input", attrs={"name": name})
    if len(inputs) != 1 or inputs[0].get("type", "").casefold() != "hidden":
        raise AuthFormError("Campo oculto ausente ou ambíguo.")
    value = inputs[0].get("value", "")
    if not value:
        raise AuthFormError("Campo oculto vazio.")
    return value


def has_login_form(html: str) -> bool:
    return is_login_page(html)


@dataclass(frozen=True)
class LoginForm:
    token: str = field(repr=False)
    action: str = field(repr=False)

    def payload(self, registration: str, password: str) -> dict[str, str]:
        return {
            "_token": self.token,
            "requisicao": self.action,
            "matricula": registration,
            "senha": password,
        }


def parse_login_form(html: str) -> LoginForm:
    soup = BeautifulSoup(html, "html.parser")
    if soup.select(".g-recaptcha, .h-captcha, iframe[src*='captcha']"):
        raise AccessBlocked("O portal exige verificação adicional; conclua o acesso manualmente.")
    candidates = [
        form
        for form in soup.find_all("form")
        if form.find("input", attrs={"name": "senha", "type": "password"})
    ]
    if len(candidates) != 1:
        raise AuthFormError("Formulário de login não reconhecido.")
    form = candidates[0]
    _safe_form(form)
    fields = form.find_all("input")
    names = [item.get("name") for item in fields]
    if set(names) != {"_token", "requisicao", "matricula", "senha"} or len(names) != 4:
        raise AccessBlocked("O login exige campos diferentes do formulário observado.")
    registration = form.find("input", attrs={"name": "matricula"})
    if registration.get("type", "text").casefold() != "text":
        raise AuthFormError("Campo de matrícula incompatível com o formulário observado.")
    return LoginForm(_hidden(form, "_token"), _hidden(form, "requisicao"))


# Não executa JavaScript: aceita somente argumentos literais da função pública.
MENU_CALL = re.compile(
    r"(?:javascript:\s*)?(?:return\s+)?executaMenu\(\s*([01])\s*,\s*"
    r"(['\"])([^'\"]+)\2\s*,\s*([012])\s*,\s*(['\"])([^'\"]*)\5\s*,\s*this\s*\)"
    r"\s*;?\s*(?:return\s+false;?)?\s*",
    re.I,
)


@dataclass(frozen=True)
class ListNavigation:
    token: str = field(repr=False)
    logged_field: str = field(repr=False)
    action: str = field(repr=False)
    target: str = field(repr=False)

    def payload(self) -> dict[str, str]:
        return {
            "flg_logado": self.logged_field,
            "_token": self.token,
            "target": self.target,
            "hRef": "",
            "requisicao": self.action,
        }


def parse_list_navigation(html: str) -> ListNavigation:
    soup = BeautifulSoup(html, "html.parser")
    forms = soup.find_all("form", attrs={"name": "formulario"})
    if len(forms) != 1:
        raise AuthFormError("Navegação autenticada ainda não reconhecida no retorno do login.")
    form = forms[0]
    _safe_form(form)
    token, logged = _hidden(form, "_token"), _hidden(form, "flg_logado")
    candidates = []
    for tag in soup.find_all(attrs={"onclick": True}):
        title = _normalized(_text(tag) + " " + str(tag.get("title", "")))
        if not all(word in title for word in ("disciplinas", "curriculo", "cursar")):
            continue
        match = MENU_CALL.fullmatch(tag["onclick"].strip())
        if match is None:
            raise AuthFormError("Ação da lista não corresponde à função pública observada.")
        blocked, action, target, href = match.group(1, 3, 4, 6)
        if blocked == "1":
            raise AccessBlocked("A consulta de disciplinas está bloqueada pelo portal.")
        if target != "0" or href:
            raise AuthFormError("A lista aponta para um fluxo diferente do observado.")
        candidates.append(ListNavigation(token, logged, action, target))
    if len(candidates) != 1:
        raise AuthFormError("Link da lista ausente ou ambíguo no retorno do login.")
    return candidates[0]
