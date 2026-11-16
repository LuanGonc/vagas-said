"""Observações são dados; não representam elegibilidade para inscrição."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class SourcePhase(StrEnum):
    UNKNOWN = "unknown"
    SID = "SID"
    SAID = "SAID"


class ParseStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"


@dataclass(frozen=True)
class Watch:
    discipline_code: str
    class_number: str
    vacancy_type: str = "UERJ"
    discipline_name: str | None = None
    enabled: bool = True

    def __post_init__(self) -> None:
        for value in (self.discipline_code, self.class_number, self.vacancy_type):
            if not value.strip():
                raise ValueError("Disciplina, turma e tipo de vaga não podem estar vazios.")


@dataclass(frozen=True)
class VacancyNumbers:
    # Cada campo mantém o significado e a tabela de origem.
    offered: int | None = None
    occupied: int | None = None
    requested_offered: int | None = None
    requested_total: int | None = None
    requested_preferential: int | None = None

    def __post_init__(self) -> None:
        for value in self.as_tuple():
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("Contagens devem ser inteiros não negativos ou None.")

    def as_tuple(self) -> tuple[int | None, ...]:
        return (
            self.offered,
            self.occupied,
            self.requested_offered,
            self.requested_total,
            self.requested_preferential,
        )


@dataclass(frozen=True)
class ClassObservation:
    watch: Watch
    captured_at: datetime
    numbers: VacancyNumbers
    source_phase: SourcePhase = SourcePhase.UNKNOWN
    parse_status: ParseStatus = ParseStatus.OK

    def __post_init__(self) -> None:
        if self.captured_at.utcoffset() is None:
            raise ValueError("O horário da captura precisa conter fuso horário.")
