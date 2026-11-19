"""Estado, eventos e fila de notificações persistidos atomicamente."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from uerj_monitor.availability import Change, compare_observations
from uerj_monitor.models import ClassObservation, Watch
from uerj_monitor.serialization import observation_from_dict, observation_to_dict, watch_key


class EventKind(StrEnum):
    NUMBERS_CHANGED = "numbers_changed"
    AVAILABILITY_DETECTED = "availability_detected"
    CLASS_DISCOVERED = "class_discovered"


@dataclass(frozen=True)
class StoredEvent:
    id: int
    kind: EventKind
    previous: ClassObservation | None
    current: ClassObservation
    policy_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "policy_id": self.policy_id,
            "previous": observation_to_dict(self.previous) if self.previous is not None else None,
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


def _event_from_row(row: sqlite3.Row) -> StoredEvent:
    return StoredEvent(
        row["id"],
        EventKind(row["kind"]),
        None if row["previous"] == "null" else _decode(row["previous"]),
        _decode(row["current"]),
        row["policy_id"],
    )


def _state_key(watch: Watch, subscription_id: int | None) -> str:
    parts = list(watch_key(watch))
    return json.dumps(
        parts if subscription_id is None else ["subscription", subscription_id, *parts]
    )


class SQLiteStore:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), timeout=5)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute("PRAGMA foreign_keys = ON")
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3, 4):
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
                CREATE TABLE IF NOT EXISTS deliveries (
                    event_id INTEGER PRIMARY KEY REFERENCES events(id),
                    destination TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT,
                    message_id INTEGER,
                    sent_at REAL
                );
                CREATE INDEX IF NOT EXISTS deliveries_due
                    ON deliveries(destination, status, available_at);
                CREATE TABLE IF NOT EXISTS delivery_limits (
                    destination TEXT PRIMARY KEY,
                    not_before REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bot_users (
                    user_id INTEGER PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES bot_users(user_id),
                    discipline_code TEXT NOT NULL,
                    discipline_name TEXT,
                    class_number TEXT NOT NULL,
                    vacancy_type TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS bot_access (
                    user_id INTEGER PRIMARY KEY REFERENCES bot_users(user_id),
                    granted_by INTEGER NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_active
                    ON subscriptions(user_id, discipline_code, class_number)
                    WHERE active = 1;
                CREATE TABLE IF NOT EXISTS subscription_events (
                    event_id INTEGER PRIMARY KEY REFERENCES events(id),
                    subscription_id INTEGER NOT NULL REFERENCES subscriptions(id)
                );
                CREATE TABLE IF NOT EXISTS bot_actions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES bot_users(user_id),
                    payload TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bot_offsets (
                    bot_id TEXT PRIMARY KEY,
                    next_offset INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS discipline_class_states (
                    user_id INTEGER NOT NULL REFERENCES bot_users(user_id),
                    discipline_code TEXT NOT NULL,
                    known_classes TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    PRIMARY KEY(user_id, discipline_code)
                );
                CREATE TABLE IF NOT EXISTS discipline_events (
                    event_id INTEGER PRIMARY KEY REFERENCES events(id),
                    user_id INTEGER NOT NULL REFERENCES bot_users(user_id),
                    discipline_code TEXT NOT NULL
                );
                PRAGMA user_version = 4;
            """)
        except Exception:
            self.connection.close()
            raise

    def __enter__(self) -> "SQLiteStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.connection.close()

    def load_latest(
        self, watch: Watch, *, subscription_id: int | None = None
    ) -> ClassObservation | None:
        row = self.connection.execute(
            "SELECT observation FROM states WHERE watch_key = ?",
            (_state_key(watch, subscription_id),),
        ).fetchone()
        return _decode(row[0]) if row else None

    def record(
        self,
        current: ClassObservation,
        *,
        available: bool | None = None,
        policy_id: str | None = None,
        delivery_destination: str | None = None,
        subscription_id: int | None = None,
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
        key = _state_key(current.watch, subscription_id)
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            if (
                subscription_id is not None
                and not self.connection.execute(
                    """SELECT 1 FROM subscriptions s JOIN bot_access a ON a.user_id = s.user_id
                   WHERE s.id = ? AND s.active = 1 AND s.discipline_code = ?
                   AND s.class_number = ? AND s.vacancy_type = ?""",
                    (subscription_id, *watch_key(current.watch)),
                ).fetchone()
            ):
                return RecordResult(Change.INCOMPARABLE)
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
                if subscription_id is not None:
                    self.connection.execute(
                        "INSERT INTO subscription_events (event_id, subscription_id) VALUES (?, ?)",
                        (event.id, subscription_id),
                    )
                if delivery_destination is not None:
                    self.connection.execute(
                        "INSERT INTO deliveries (event_id, destination) VALUES (?, ?)",
                        (event.id, delivery_destination),
                    )
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

    def record_classes(
        self, user_id: int, observations: list[ClassObservation], *, delivery_destination: str
    ) -> tuple[StoredEvent, ...]:
        """Guarda turmas conhecidas e enfileira novidades por pessoa/disciplina atomicamente."""
        classes = {
            item.watch.class_number: item
            for item in observations
            if item.watch.vacancy_type == "UERJ"
        }
        if not classes:
            return ()
        current = next(iter(classes.values()))
        code, captured_at, phase = (
            current.watch.discipline_code,
            current.captured_at,
            current.source_phase,
        )
        if any(
            (item.watch.discipline_code, item.captured_at, item.source_phase)
            != (code, captured_at, phase)
            for item in classes.values()
        ):
            raise ValueError("A lista de turmas precisa pertencer à mesma consulta.")
        events = []
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            if not self.connection.execute(
                """SELECT 1 FROM subscriptions s JOIN bot_access a ON a.user_id = s.user_id
                   WHERE s.user_id = ? AND s.discipline_code = ? AND s.active = 1""",
                (user_id, code),
            ).fetchone():
                return ()
            row = self.connection.execute(
                "SELECT * FROM discipline_class_states WHERE user_id = ? AND discipline_code = ?",
                (user_id, code),
            ).fetchone()
            if row and captured_at <= datetime.fromisoformat(row["captured_at"]):
                return ()
            known = set(json.loads(row["known_classes"])) if row and row["phase"] == phase else None
            if known is not None:
                for number in sorted(classes.keys() - known):
                    item = classes[number]
                    cursor = self.connection.execute(
                        "INSERT INTO events (kind, previous, current) VALUES (?, ?, ?)",
                        (EventKind.CLASS_DISCOVERED.value, "null", _encode(item)),
                    )
                    event = StoredEvent(cursor.lastrowid, EventKind.CLASS_DISCOVERED, None, item)
                    self.connection.execute(
                        "INSERT INTO discipline_events VALUES (?, ?, ?)", (event.id, user_id, code)
                    )
                    self.connection.execute(
                        "INSERT INTO deliveries (event_id, destination) VALUES (?, ?)",
                        (event.id, delivery_destination),
                    )
                    events.append(event)
            self.connection.execute(
                """INSERT INTO discipline_class_states VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, discipline_code) DO UPDATE SET
                     known_classes = excluded.known_classes, captured_at = excluded.captured_at,
                     phase = excluded.phase""",
                (
                    user_id,
                    code,
                    json.dumps(sorted(set(classes) | (known or set()))),
                    captured_at.isoformat(),
                    phase.value,
                ),
            )
        return tuple(events)

    def list_events(self, limit: int = 100) -> list[StoredEvent]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("O limite de eventos deve estar entre 1 e 1000.")
        rows = self.connection.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_event_from_row(row) for row in rows]
