"""Catálogo de desenvolvimento; não contém seletores ou requisições presumidos da UERJ."""

import unicodedata
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from uerj_monitor.config import MonitorConfig
from uerj_monitor.models import ClassObservation, Watch
from uerj_monitor.monitor import LocalHtmlClient
from uerj_monitor.parser import parse_classes


def normalized(text: str) -> str:
    return " ".join(
        "".join(
            char
            for char in unicodedata.normalize("NFKD", text.casefold())
            if not unicodedata.combining(char)
        ).split()
    )


@dataclass(frozen=True)
class Discipline:
    code: str
    name: str


class LocalCatalog:
    def __init__(self, config: MonitorConfig) -> None:
        self.client = LocalHtmlClient(config.sources, config.encoding)
        self.phase = config.phase
        self.disciplines = {
            code: Discipline(code, config.discipline_names[code]) for code in config.sources
        }

    def search(self, query: str, user_id: int) -> list[Discipline]:
        # O contexto do usuário permitirá trocar o adaptador sem decidir a sessão agora.
        terms = normalized(query).split()
        return [
            discipline
            for discipline in sorted(self.disciplines.values(), key=lambda d: d.code)
            if all(term in normalized(f"{discipline.code} {discipline.name}") for term in terms)
        ]

    def observe(self, code: str, user_id: int, captured_at: datetime) -> list[ClassObservation]:
        discipline = self.disciplines.get(code)
        if discipline is None:
            raise ValueError("Disciplina ausente do catálogo.")
        observations = parse_classes(
            self.client.fetch_discipline_classes(code),
            discipline_code=code,
            captured_at=captured_at,
            source_phase=self.phase,
        )
        return [
            replace(item, watch=replace(item.watch, discipline_name=discipline.name))
            for item in observations
        ]

    def classes(self, code: str, user_id: int) -> list[Watch]:
        observations = self.observe(code, user_id, datetime.now(UTC))
        return [item.watch for item in observations if item.watch.vacancy_type == "UERJ"]
