"""Estado e eventos locais atômicos. Nenhuma mensagem é enviada nesta etapa."""

import json
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from uerj_monitor.availability import Change, compare_observations
from uerj_monitor.models import ClassObservation, Watch
from uerj_monitor.serialization import observation_from_dict, observation_to_dict, watch_key


class EventKind(StrEnum):
    NUMBERS_CHANGED = "numbers_changed"
    AVAILABILITY_DETECTED = "availability_detected"


@dataclass(frozen=True)
class StoredEvent:
    id: int
    kind: EventKind
    previous: ClassObservation
    current: ClassObservation
    policy_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "policy_id": self.policy_id,
            "previous": observation_to_dict(self.previous),
            "current": observation_to_dict(self.current),
        }


@dataclass(frozen=True)
class RecordResult:
    change: Change
    event: StoredEvent | None = None


def _encode(observation: ClassObservation) -> str:
    return json.dumps(observation_to_dict(observation), ensure_ascii=True)


def _decode(payload: str) -> ClassObservation:
    return observation_from_dict(json.loads(payload))


class SQLiteStore:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), timeout=5)
        self.connection.row_factory = sqlite3.Row
        try:
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Versão de banco não suportada.")
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS states (
                    watch_key TEXT PRIMARY KEY,
                    observation TEXT NOT NULL,
                    available INTEGER,
                    policy_id TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    previous TEXT NOT NULL,
                    current TEXT NOT NULL,
                    policy_id TEXT
                );
                PRAGMA user_version = 1;
            """)
        except Exception:
            self.connection.close()
            raise

    def __enter__(self) -> "SQLiteStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.connection.close()

    def load_latest(self, watch: Watch) -> ClassObservation | None:
        row = self.connection.execute(
            "SELECT observation FROM states WHERE watch_key = ?",
            (json.dumps(watch_key(watch)),),
        ).fetchone()
        return _decode(row[0]) if row else None

    def record(
        self,
        current: ClassObservation,
        *,
        available: bool | None = None,
        policy_id: str | None = None,
    ) -> RecordResult:
        """Atualiza baseline e evento na mesma transação.

        Sem policy_id, registra apenas mudanças numéricas. Uma regra injetada
        pode informar disponibilidade; nenhuma regra do portal foi implementada.
        None preserva o último estado de disponibilidade conhecido na mesma fase/regra.
        """
        if available is not None and type(available) is not bool:
            raise ValueError("Disponibilidade deve ser bool ou None.")
        if available is not None and policy_id is None:
            raise ValueError("Disponibilidade exige uma regra identificada.")
        if policy_id is not None and not policy_id.strip():
            raise ValueError("A regra precisa de um identificador não vazio.")
        key = json.dumps(watch_key(current.watch))
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                "SELECT * FROM states WHERE watch_key = ?", (key,)
            ).fetchone()
            previous = _decode(row["observation"]) if row else None
            change = compare_observations(previous, current)
            if previous is not None and current.captured_at <= previous.captured_at:
                return RecordResult(Change.INCOMPARABLE)
            same_context = (
                previous is not None
                and previous.source_phase == current.source_phase
                and row["policy_id"] == policy_id
            )
            last_available = row["available"] if same_context else None
            next_available = available if available is not None else last_available
            kind = None
            if policy_id is None and change == Change.NUMBERS_CHANGED:
                kind = EventKind.NUMBERS_CHANGED
            elif policy_id is not None and last_available == 0 and available is True:
                kind = EventKind.AVAILABILITY_DETECTED
            event = None
            if kind is not None:
                assert previous is not None
                cursor = self.connection.execute(
                    "INSERT INTO events (kind, previous, current, policy_id) VALUES (?, ?, ?, ?)",
                    (kind.value, _encode(previous), _encode(current), policy_id),
                )
                event = StoredEvent(cursor.lastrowid, kind, previous, current, policy_id)
            self.connection.execute(
                """INSERT INTO states (watch_key, observation, available, policy_id)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(watch_key) DO UPDATE SET
                     observation = excluded.observation,
                     available = excluded.available,
                     policy_id = excluded.policy_id""",
                (key, _encode(current), next_available, policy_id),
            )
        return RecordResult(change, event)

    def list_events(self, limit: int = 100) -> list[StoredEvent]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("O limite de eventos deve estar entre 1 e 1000.")
        rows = self.connection.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            StoredEvent(
                row["id"],
                EventKind(row["kind"]),
                _decode(row["previous"]),
                _decode(row["current"]),
                row["policy_id"],
            )
            for row in rows
        ]
