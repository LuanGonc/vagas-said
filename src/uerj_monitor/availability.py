"""Comparação conservadora de observações, sem regra de matrícula presumida."""

from enum import StrEnum

from uerj_monitor.models import ClassObservation


class Change(StrEnum):
    BASELINE = "baseline"
    UNCHANGED = "unchanged"
    NUMBERS_CHANGED = "numbers_changed"
    INCOMPARABLE = "incomparable"


def compare_observations(previous: ClassObservation | None, current: ClassObservation) -> Change:
    """Detecta mudanças em campos conhecidos nas duas capturas.

    Ausência de dados e mudança de fase não significam alteração de vagas.
    Quem chamar decide persistência/entrega; esta função não envia alertas.
    """
    if previous is None:
        return Change.BASELINE
    previous_key = (
        previous.watch.discipline_code,
        previous.watch.class_number,
        previous.watch.vacancy_type,
    )
    current_key = (
        current.watch.discipline_code,
        current.watch.class_number,
        current.watch.vacancy_type,
    )
    if previous_key != current_key:
        raise ValueError("Só é possível comparar a mesma disciplina, turma e tipo de vaga.")
    if current.captured_at <= previous.captured_at:
        return Change.INCOMPARABLE
    if previous.source_phase != current.source_phase:
        return Change.INCOMPARABLE
    pairs = tuple(zip(previous.numbers.as_tuple(), current.numbers.as_tuple(), strict=True))
    if any(before != after for before, after in pairs if before is not None and after is not None):
        return Change.NUMBERS_CHANGED
    if any(before is None or after is None for before, after in pairs):
        return Change.INCOMPARABLE
    return Change.UNCHANGED
