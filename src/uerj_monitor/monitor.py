"""Uma consulta por disciplina, sem concorrência, com seleção explícita de turmas."""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from uerj_monitor.models import ClassObservation, SourcePhase, Watch
from uerj_monitor.parser import ParseError, parse_classes
from uerj_monitor.portal_client import PortalClient
from uerj_monitor.serialization import watch_key
from uerj_monitor.storage import SQLiteStore, StoredEvent


class SourceError(RuntimeError):
    """Consulta falhou. Não contém a resposta nem dados de sessão."""


class LocalHtmlClient:
    """Adaptador de desenvolvimento. Não é um cliente HTTP do Aluno Online."""

    def __init__(self, sources: dict[str, Path], encoding: str = "utf-8") -> None:
        self.sources = sources
        self.encoding = encoding

    def fetch_discipline_classes(self, discipline_code: str) -> str:
        try:
            return self.sources[discipline_code].read_text(encoding=self.encoding)
        except (KeyError, OSError, UnicodeError, LookupError) as error:
            raise SourceError("Falha ao ler a fonte HTML local.") from error


class AvailabilityRule(Protocol):
    """Extensão para uma regra comprovada; hoje é exercitada apenas por testes."""

    policy_id: str

    def evaluate(self, observation: ClassObservation) -> bool | None:
        """True/False somente com evidência suficiente; None significa desconhecido."""
        ...


@dataclass(frozen=True)
class PollFailure:
    discipline_code: str
    reason: str


@dataclass(frozen=True)
class CycleResult:
    observed: int
    failures: tuple[PollFailure, ...]
    events: tuple[StoredEvent, ...]

    def to_dict(self) -> dict:
        return {
            "observed": self.observed,
            "failures": [
                {"discipline_code": failure.discipline_code, "reason": failure.reason}
                for failure in self.failures
            ],
            "events": [event.to_dict() for event in self.events],
        }


class Monitor:
    def __init__(
        self,
        client: PortalClient,
        store: SQLiteStore,
        watches: tuple[Watch, ...],
        *,
        phase: SourcePhase = SourcePhase.UNKNOWN,
        rule: AvailabilityRule | None = None,
        delivery_destination: str | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.client, self.store = client, store
        self.phase, self.rule, self.clock = phase, rule, clock
        self.delivery_destination = delivery_destination
        self.groups: dict[str, list[Watch]] = defaultdict(list)
        seen = set()
        for watch in watches:
            if not watch.enabled:
                continue
            if watch_key(watch) in seen:
                raise ValueError("Turma repetida no monitor.")
            seen.add(watch_key(watch))
            self.groups[watch.discipline_code].append(watch)
        if not self.groups:
            raise ValueError("O monitor precisa de pelo menos uma turma ativa.")

    def poll_once(self) -> CycleResult:
        observed, failures, events = 0, [], []
        for discipline_code, watches in self.groups.items():
            try:
                html = self.client.fetch_discipline_classes(discipline_code)
                observations = parse_classes(
                    html,
                    discipline_code=discipline_code,
                    captured_at=self.clock(),
                    source_phase=self.phase,
                )
            except (SourceError, OSError, TimeoutError):
                failures.append(PollFailure(discipline_code, "source_error"))
                continue
            except ParseError:
                failures.append(PollFailure(discipline_code, "parse_error"))
                continue
            by_key = {watch_key(item.watch): item for item in observations}
            if any(watch_key(watch) not in by_key for watch in watches):
                # Ausência de turma não vira zero nem altera seu último estado conhecido.
                failures.append(PollFailure(discipline_code, "missing_watch"))
                continue
            for watch in watches:
                current = replace(by_key[watch_key(watch)], watch=watch)
                result = self.store.record(
                    current,
                    available=self.rule.evaluate(current) if self.rule else None,
                    policy_id=self.rule.policy_id if self.rule else None,
                    delivery_destination=self.delivery_destination,
                )
                observed += 1
                if result.event is not None:
                    events.append(result.event)
        return CycleResult(observed, tuple(failures), tuple(events))
