"""Parser do contrato HTML SINTÉTICO descrito em docs/protocolo.md.

Não foi validado no portal real. Estrutura desconhecida causa ParseError.
"""

import re
import unicodedata
from datetime import UTC, datetime

from bs4 import BeautifulSoup, Tag

from uerj_monitor.models import (
    ClassObservation,
    ParseStatus,
    SourcePhase,
    VacancyNumbers,
    Watch,
)

CURRENT_CAPTION = "vagas atualizadas da turma"
REQUEST_CAPTION = "vagas para solicitacao de inscricao"
CURRENT_HEADERS = {"tipo de vaga", "oferecidas", "ocupadas"}
REQUEST_HEADERS = {
    "tipo de vaga",
    "oferecidas",
    "solicitadas total",
    "solicitadas preferenciais",
}


class ParseError(ValueError):
    """HTML incompatível; a mensagem não contém trechos do HTML recebido."""


def _text(tag: Tag) -> str:
    return " ".join(tag.get_text(" ", strip=True).split())


def _normalized(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return " ".join(
        "".join(char for char in decomposed if not unicodedata.combining(char)).lower().split()
    )


def _span(cell: Tag, attribute: str) -> int:
    value = str(cell.get(attribute, "1"))
    if not re.fullmatch(r"[0-9]{1,2}", value) or not 1 <= int(value) <= 20:
        raise ParseError("Expansão de cabeçalho não suportada.")
    return int(value)


def _headers(table: Tag) -> list[str]:
    head = table.find("thead", recursive=False)
    if head is None:
        raise ParseError("Tabela sem cabeçalho explícito.")
    rows = head.find_all("tr", recursive=False)
    grid: dict[tuple[int, int], str] = {}
    for row_index, row in enumerate(rows):
        column_index = 0
        for cell in row.find_all("th", recursive=False):
            while (row_index, column_index) in grid:
                column_index += 1
            height, width = _span(cell, "rowspan"), _span(cell, "colspan")
            if row_index + height > len(rows):
                raise ParseError("Cabeçalho com rowspan fora da tabela.")
            for offset_y in range(height):
                for offset_x in range(width):
                    position = (row_index + offset_y, column_index + offset_x)
                    if position in grid:
                        raise ParseError("Cabeçalhos sobrepostos.")
                    grid[position] = _normalized(_text(cell))
            column_index += width
    if not grid:
        raise ParseError("Tabela sem colunas identificáveis.")
    width = max(column for _, column in grid) + 1
    if len(grid) != len(rows) * width:
        raise ParseError("Cabeçalho incompleto.")
    labels = []
    for column in range(width):
        parts = list(dict.fromkeys(grid[row, column] for row in range(len(rows))))
        labels.append(" ".join(parts))
    return labels


def _number(cell: Tag) -> int | None:
    if cell.find(["script", "style"]) is not None:
        raise ParseError("Conteúdo inesperado em célula de contagem.")
    value = _text(cell)
    if value in {"", "-", "—"}:
        return None
    if not re.fullmatch(r"[0-9]+", value):
        raise ParseError("Contagem de vagas inválida; revise a amostra localmente.")
    return int(value)


def _read_table(table: Tag, expected: set[str]) -> dict[str, dict[str, int | None]]:
    headers = _headers(table)
    if set(headers) != expected or len(headers) != len(expected):
        raise ParseError("Colunas inesperadas na tabela de vagas.")
    bodies = table.find_all("tbody", recursive=False)
    result: dict[str, dict[str, int | None]] = {}
    for body in bodies:
        for row in body.find_all("tr", recursive=False):
            cells = row.find_all(["td", "th"], recursive=False)
            if len(cells) != len(headers):
                raise ParseError("Linha com quantidade inesperada de células.")
            if any(_span(cell, "rowspan") != 1 or _span(cell, "colspan") != 1 for cell in cells):
                raise ParseError("Células de dados mescladas não são suportadas.")
            mapped = dict(zip(headers, cells, strict=True))
            vacancy_type = _text(mapped.pop("tipo de vaga")).upper()
            if not vacancy_type or vacancy_type in result:
                raise ParseError("Tipo de vaga ausente ou repetido na mesma tabela.")
            result[vacancy_type] = {key: _number(cell) for key, cell in mapped.items()}
    if not result:
        raise ParseError("Tabela sem linhas de vagas reconhecíveis.")
    return result


def parse_classes(
    html: str,
    *,
    discipline_code: str,
    captured_at: datetime | None = None,
    source_phase: SourcePhase = SourcePhase.UNKNOWN,
) -> list[ClassObservation]:
    """Extrai uma observação por turma/tipo do formato sintético suportado.

    Fase e disciplina são fornecidas pelo chamador; não são inferidas do HTML.
    Campos vazios permanecem None. Erros invalidam toda a leitura.
    """
    if not discipline_code.strip():
        raise ValueError("Informe o código da disciplina.")
    timestamp = captured_at if captured_at is not None else datetime.now(UTC)
    soup = BeautifulSoup(html, "html.parser")
    observations = []
    seen_classes: set[str] = set()
    for section in soup.find_all("section"):
        heading = section.find(["h2", "h3"], recursive=False)
        if heading is None:
            continue
        match = re.fullmatch(r"turma\s*:?\s+(\S+)", _normalized(_text(heading)))
        if match is None:
            continue
        class_number = match.group(1).upper()
        if class_number in seen_classes:
            raise ParseError("Número de turma repetido; associação ambígua.")
        seen_classes.add(class_number)
        tables: dict[str, dict[str, dict[str, int | None]]] = {}
        for table in section.find_all("table", recursive=False):
            caption = table.find("caption", recursive=False)
            label = _normalized(_text(caption)) if caption else ""
            if label not in {CURRENT_CAPTION, REQUEST_CAPTION}:
                raise ParseError("Tabela sem identificação conhecida na seção da turma.")
            if label in tables:
                raise ParseError("Tabela de vagas repetida na mesma turma.")
            expected = CURRENT_HEADERS if label == CURRENT_CAPTION else REQUEST_HEADERS
            tables[label] = _read_table(table, expected)
        if not tables:
            raise ParseError("Turma sem tabelas de vagas reconhecíveis.")
        current = tables.get(CURRENT_CAPTION, {})
        requests = tables.get(REQUEST_CAPTION, {})
        for vacancy_type in dict.fromkeys([*current, *requests]):
            current_row = current.get(vacancy_type, {})
            request_row = requests.get(vacancy_type, {})
            numbers = VacancyNumbers(
                offered=current_row.get("oferecidas"),
                occupied=current_row.get("ocupadas"),
                requested_offered=request_row.get("oferecidas"),
                requested_total=request_row.get("solicitadas total"),
                requested_preferential=request_row.get("solicitadas preferenciais"),
            )
            status = ParseStatus.PARTIAL if None in numbers.as_tuple() else ParseStatus.OK
            observations.append(
                ClassObservation(
                    watch=Watch(discipline_code, class_number, vacancy_type),
                    captured_at=timestamp,
                    numbers=numbers,
                    source_phase=source_phase,
                    parse_status=status,
                )
            )
    if not observations:
        raise ParseError("Nenhuma turma reconhecida pelo contrato HTML sintético.")
    return observations
