"""Serialização apenas dos campos normalizados; HTML e sessão não são persistidos."""

from dataclasses import asdict
from datetime import datetime
from typing import Any

from uerj_monitor.models import (
    ClassObservation,
    ParseStatus,
    SourcePhase,
    VacancyNumbers,
    Watch,
)


def observation_to_dict(observation: ClassObservation) -> dict[str, Any]:
    payload = asdict(observation)
    payload["captured_at"] = observation.captured_at.isoformat()
    return payload


def observation_from_dict(payload: dict[str, Any]) -> ClassObservation:
    return ClassObservation(
        watch=Watch(**payload["watch"]),
        captured_at=datetime.fromisoformat(payload["captured_at"]),
        numbers=VacancyNumbers(**payload["numbers"]),
        source_phase=SourcePhase(payload["source_phase"]),
        parse_status=ParseStatus(payload["parse_status"]),
    )


def watch_key(watch: Watch) -> tuple[str, str, str]:
    return watch.discipline_code, watch.class_number, watch.vacancy_type
