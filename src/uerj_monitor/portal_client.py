"""Contrato e consulta HTTP com sessão autorizada e formulário real previamente obtido."""

from typing import Protocol

import httpx

from uerj_monitor.discipline_parser import (
    PORTAL_URL,
    parse_discipline_list,
    parse_navigation_fields,
)
from uerj_monitor.parser import ParseError, parse_classes


class PortalClient(Protocol):
    def fetch_discipline_classes(self, discipline_code: str) -> str:
        """Retorna HTML em uma sessão autorizada, ou lança erro de consulta.

        Implementar somente após validar o fluxo descrito em docs/protocolo.md.
        Falhas de sessão/rede nunca devem retornar HTML vazio como sucesso.
        """
        ...


class SourceError(RuntimeError):
    """Consulta falhou sem expor resposta, requisição ou dados de sessão."""


class PortalQueryError(SourceError):
    """Falha sanitizada; não reproduz requisição, resposta ou dados de sessão."""


class ObservedPortalClient:
    """Não autentica: o chamador fornece sessão e HTML da lista autorizados.

    Formulários novos devem vir da navegação real, sem reconstruir campos opacos.
    Não reutilizar esta instância entre pessoas ou iniciar polling sem validar a sessão.
    """

    def __init__(self, session: httpx.Client, list_html: str) -> None:
        self.session = session
        self.disciplines = {item.code: item for item in parse_discipline_list(list_html)}
        self._fields = parse_navigation_fields(list_html)

    def fetch_discipline_classes(self, discipline_code: str) -> str:
        discipline = self.disciplines.get(discipline_code)
        if discipline is None or not discipline.has_classes:
            raise PortalQueryError("Disciplina ausente ou sem turma indicada na lista consultada.")
        try:
            response = self.session.post(
                PORTAL_URL,
                data=self._fields.class_query(discipline.internal_id),
                timeout=15,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise PortalQueryError("Falha de rede na consulta de turmas.") from None
        if response.status_code != 200:
            raise PortalQueryError("O portal não retornou uma consulta bem-sucedida.")
        from bs4 import BeautifulSoup

        encoding = (
            response.charset_encoding
            or BeautifulSoup(response.content, "html.parser").original_encoding
        )
        html = response.content.decode(encoding or "utf-8", errors="replace")
        try:
            parse_classes(html, discipline_code=discipline_code)
        except ParseError:
            raise PortalQueryError(
                "Resposta incompatível ou sessão expirada; refaça a navegação local."
            ) from None
        return html
