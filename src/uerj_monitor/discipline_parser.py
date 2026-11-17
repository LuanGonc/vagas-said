"""Lista de disciplinas no formato verificado no HAR de 17/11/2026.

Extrai apenas código, nome, identificador de consulta e oferta de turma. Não extrai
situação acadêmica, período cursado, resultados ou demais dados pessoais da grade.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from uerj_monitor.parser import ParseError, _normalized, _text

PORTAL_URL = "https://www.alunoonline.uerj.br/requisicaoaluno/"
HEADERS = (
    "disciplina",
    "periodo",
    "atendida?",
    "tipo",
    "ramif.",
    "cred.",
    "ch total",
    "trava credito",
    "turma no periodo?",
)
LINK = re.compile(r"javascript:consultarDisciplina\(output,\s*([0-9]+)\);\s*return false;")
CODE_NAME = re.compile(r"([A-Z]{3}[0-9]{2}-[0-9]{5})\s+(.+)")


@dataclass(frozen=True)
class PortalDiscipline:
    code: str
    name: str
    internal_id: str
    has_classes: bool


@dataclass(frozen=True)
class NavigationFields:
    token: str = field(repr=False)
    action: str = field(repr=False)
    registration: str = field(repr=False)

    def class_query(self, internal_id: str) -> dict[str, str]:
        return {
            "_token": self.token,
            "requisicao": self.action,
            "matricula": self.registration,
            "disciplinas[0]": internal_id,
        }


def parse_discipline_list(html: str) -> list[PortalDiscipline]:
    soup = BeautifulSoup(html, "html.parser")
    matches = []
    for table in soup.find_all("table"):
        headers = [cell for cell in table.find_all("th") if cell.find_parent("table") is table]
        if tuple(_normalized(_text(cell)) for cell in headers) == HEADERS:
            matches.append(table)
    if len(matches) != 1:
        raise ParseError("Tabela da lista de disciplinas ausente ou ambígua.")
    table = matches[0]
    disciplines = []
    seen_codes, seen_ids = set(), set()
    for row in table.find_all("tr"):
        if row.find_parent("table") is not table:
            continue
        if row.find("th", recursive=False):
            continue
        cells = row.find_all("td", recursive=False)
        if len(cells) != len(HEADERS):
            raise ParseError("Linha de disciplina incompatível com o formato validado.")
        name_span = cells[0].find("span")
        match = CODE_NAME.fullmatch(_text(name_span)) if name_span else None
        if match is None:
            raise ParseError("Código e nome da disciplina não reconhecidos.")
        code, name = match.groups()
        identifiers = set()
        for anchor in row.find_all("a"):
            link = LINK.fullmatch(anchor.get("onclick", ""))
            if link is None:
                raise ParseError("Link de disciplina fora do protocolo observado.")
            identifiers.add(link.group(1))
        if len(identifiers) != 1:
            raise ParseError("Identificador de consulta ausente ou ambíguo.")
        internal_id = identifiers.pop()
        available = _normalized(_text(cells[-1]))
        if available not in {"sim", "nao"}:
            raise ParseError("Indicador de turma no período não reconhecido.")
        if code in seen_codes or internal_id in seen_ids:
            raise ParseError("Disciplina ou identificador repetido na lista.")
        seen_codes.add(code)
        seen_ids.add(internal_id)
        disciplines.append(PortalDiscipline(code, name, internal_id, available == "sim"))
    if not disciplines:
        raise ParseError("A lista não contém disciplinas reconhecíveis.")
    return disciplines


def parse_navigation_fields(html: str) -> NavigationFields:
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for form in soup.find_all("form"):
        fields = {}
        for item in form.find_all("input"):
            if item.get("name") in {"_token", "requisicao", "matricula"}:
                if item.get("name") in fields or item.get("type", "").lower() != "hidden":
                    raise ParseError("Campos de navegação duplicados ou inesperados.")
                fields[item["name"]] = item.get("value", "")
        if set(fields) != {"_token", "requisicao", "matricula"}:
            continue
        if form.get("method", "get").lower() != "post":
            raise ParseError("Método do formulário não corresponde à consulta validada.")
        if urljoin(PORTAL_URL, form.get("action", "")) != PORTAL_URL:
            raise ParseError("Destino do formulário não corresponde ao portal validado.")
        if not all(fields.values()):
            raise ParseError("Campos de navegação ausentes ou vazios.")
        candidates.append(fields)
    if len(candidates) != 1:
        raise ParseError("Formulário de consulta ausente ou ambíguo.")
    fields = candidates[0]
    return NavigationFields(fields["_token"], fields["requisicao"], fields["matricula"])
