"""Catálogos local e central; a identidade Telegram pertence às assinaturas."""

import unicodedata
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol

from uerj_monitor.config import MonitorConfig
from uerj_monitor.models import ClassObservation, SourcePhase, Watch
from uerj_monitor.monitor import LocalHtmlClient
from uerj_monitor.parser import parse_classes
from uerj_monitor.portal_client import DisciplineUnavailable
from uerj_monitor.portal_session import AuthenticatedPortalSession


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


class Catalog(Protocol):
    is_demo: bool
    shared_queries: bool

    def search(self, query: str, user_id: int) -> list[Discipline]: ...

    def observe(self, code: str, user_id: int, captured_at: datetime) -> list[ClassObservation]: ...

    def classes(self, code: str, user_id: int) -> list[Watch]: ...


def _search(disciplines: list[Discipline], query: str) -> list[Discipline]:
    terms = normalized(query).split()
    return [
        discipline
        for discipline in sorted(disciplines, key=lambda d: d.code)
        if all(term in normalized(f"{discipline.code} {discipline.name}") for term in terms)
    ]


class LocalCatalog:
    is_demo = True
    shared_queries = False

    def __init__(self, config: MonitorConfig) -> None:
        self.client = LocalHtmlClient(config.sources, config.encoding)
        self.phase = config.phase
        self.disciplines = {
            code: Discipline(code, config.discipline_names[code]) for code in config.sources
        }

    def search(self, query: str, user_id: int) -> list[Discipline]:
        return _search(list(self.disciplines.values()), query)

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


class CentralPortalCatalog:
    """Uma única sessão do responsável para todos os convidados autorizados."""

    is_demo = False
    shared_queries = True

    def __init__(
        self, session: AuthenticatedPortalSession, phase: SourcePhase = SourcePhase.UNKNOWN
    ) -> None:
        self.session, self.phase = session, phase

    def search(self, query: str, user_id: int) -> list[Discipline]:
        disciplines = [
            Discipline(item.code, item.name)
            for item in self.session.refresh_disciplines()
            if item.has_classes
        ]
        return _search(disciplines, query)

    def observe(self, code: str, user_id: int, captured_at: datetime) -> list[ClassObservation]:
        try:
            html = self.session.fetch_discipline_classes(code)
        except DisciplineUnavailable:
            return []
        discipline = next(item for item in self.session.disciplines if item.code == code)
        observations = parse_classes(
            html, discipline_code=code, captured_at=captured_at, source_phase=self.phase
        )
        return [
            replace(item, watch=replace(item.watch, discipline_name=discipline.name))
            for item in observations
        ]

    def classes(self, code: str, user_id: int) -> list[Watch]:
        observations = self.observe(code, user_id, datetime.now(UTC))
        return [item.watch for item in observations if item.watch.vacancy_type == "UERJ"]
