"""Assinaturas privadas com limite transacional de dez turmas por pessoa."""

import json
import secrets
from dataclasses import dataclass
from enum import StrEnum

from uerj_monitor.models import Watch
from uerj_monitor.storage import SQLiteStore

MAX_SUBSCRIPTIONS = 10


class SubscribeResult(StrEnum):
    ADDED = "added"
    ALREADY_ACTIVE = "already_active"
    LIMIT_REACHED = "limit_reached"


@dataclass(frozen=True)
class Subscription:
    id: int
    user_id: int
    watch: Watch


class SubscriptionRepository:
    def __init__(self, store: SQLiteStore) -> None:
        self.connection = store.connection

    def ensure_user(self, user_id: int) -> None:
        if type(user_id) is not int or not 0 < user_id < 2**63:
            raise ValueError("Identificador de usuário inválido.")
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO bot_users VALUES (?)", (user_id,))

    def authorized(self, user_id: int) -> bool:
        return (
            self.connection.execute(
                "SELECT 1 FROM bot_access WHERE user_id = ?", (user_id,)
            ).fetchone()
            is not None
        )

    def grant(self, user_id: int, granted_by: int) -> None:
        self.ensure_user(user_id)
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO bot_access VALUES (?, ?)", (user_id, granted_by)
            )

    def revoke(self, user_id: int) -> None:
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute("DELETE FROM bot_access WHERE user_id = ?", (user_id,))
            self.connection.execute(
                "UPDATE subscriptions SET active = 0 WHERE user_id = ?", (user_id,)
            )
            self.connection.execute(
                "DELETE FROM discipline_class_states WHERE user_id = ?", (user_id,)
            )
            self._cancel_discipline_events(user_id)
            self.connection.execute(
                """UPDATE deliveries SET status = 'cancelled' WHERE event_id IN (
                     SELECT se.event_id FROM subscription_events se
                     JOIN subscriptions s ON s.id = se.subscription_id WHERE s.user_id = ?)
                   AND status NOT IN ('sent', 'sending')""",
                (user_id,),
            )

    def subscribe(self, user_id: int, watch: Watch) -> SubscribeResult:
        self.ensure_user(user_id)
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            if not self.authorized(user_id):
                raise PermissionError("O responsável precisa autorizar o acesso.")
            exists = self.connection.execute(
                """SELECT 1 FROM subscriptions WHERE user_id = ? AND discipline_code = ?
                   AND class_number = ? AND active = 1""",
                (user_id, watch.discipline_code, watch.class_number),
            ).fetchone()
            if exists:
                return SubscribeResult.ALREADY_ACTIVE
            count = self.connection.execute(
                "SELECT count(*) FROM subscriptions WHERE user_id = ? AND active = 1", (user_id,)
            ).fetchone()[0]
            if count >= MAX_SUBSCRIPTIONS:
                return SubscribeResult.LIMIT_REACHED
            self.connection.execute(
                """INSERT INTO subscriptions
                   (user_id, discipline_code, discipline_name, class_number, vacancy_type)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    user_id,
                    watch.discipline_code,
                    watch.discipline_name,
                    watch.class_number,
                    watch.vacancy_type,
                ),
            )
        return SubscribeResult.ADDED

    def list_active(self, user_id: int | None = None) -> list[Subscription]:
        sql = """SELECT * FROM subscriptions WHERE active = 1 AND EXISTS (
                 SELECT 1 FROM bot_access WHERE bot_access.user_id = subscriptions.user_id)"""
        parameters = ()
        if user_id is not None:
            sql += " AND user_id = ?"
            parameters = (user_id,)
        rows = self.connection.execute(sql + " ORDER BY id", parameters).fetchall()
        return [
            Subscription(
                row["id"],
                row["user_id"],
                Watch(
                    row["discipline_code"],
                    row["class_number"],
                    row["vacancy_type"],
                    row["discipline_name"],
                ),
            )
            for row in rows
        ]

    def unsubscribe(self, user_id: int, subscription_id: int) -> bool:
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            cursor = self.connection.execute(
                "UPDATE subscriptions SET active = 0 WHERE id = ? AND user_id = ? AND active = 1",
                (subscription_id, user_id),
            )
            if not cursor.rowcount:
                return False
            code = self.connection.execute(
                "SELECT discipline_code FROM subscriptions WHERE id = ?", (subscription_id,)
            ).fetchone()[0]
            if not self.connection.execute(
                """SELECT 1 FROM subscriptions WHERE user_id = ? AND discipline_code = ?
                   AND active = 1""",
                (user_id, code),
            ).fetchone():
                self.connection.execute(
                    "DELETE FROM discipline_class_states WHERE user_id = ? AND discipline_code = ?",
                    (user_id, code),
                )
                self._cancel_discipline_events(user_id, code)
            self.connection.execute(
                """UPDATE deliveries SET status = 'cancelled' WHERE event_id IN
                   (SELECT event_id FROM subscription_events WHERE subscription_id = ?)
                   AND status NOT IN ('sent', 'sending')""",
                (subscription_id,),
            )
            return True

    def _cancel_discipline_events(self, user_id: int, code: str | None = None) -> None:
        sql = """UPDATE deliveries SET status = 'cancelled' WHERE event_id IN (
                 SELECT event_id FROM discipline_events WHERE user_id = ?"""
        parameters = [user_id]
        if code is not None:
            sql += " AND discipline_code = ?"
            parameters.append(code)
        self.connection.execute(sql + ") AND status NOT IN ('sent', 'sending')", parameters)

    def action(self, user_id: int, payload: dict, now: float) -> str:
        self.ensure_user(user_id)
        token = secrets.token_urlsafe(12)
        with self.connection:
            self.connection.execute("DELETE FROM bot_actions WHERE expires_at <= ?", (now,))
            self.connection.execute(
                "INSERT INTO bot_actions VALUES (?, ?, ?, ?)",
                (token, user_id, json.dumps(payload), now + 900),
            )
        return "a:" + token

    def resolve_action(self, user_id: int, token: str, now: float) -> dict | None:
        row = self.connection.execute(
            "SELECT payload FROM bot_actions WHERE token = ? AND user_id = ? AND expires_at > ?",
            (token.removeprefix("a:"), user_id, now),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def offset(self, bot_id: str) -> int | None:
        row = self.connection.execute(
            "SELECT next_offset FROM bot_offsets WHERE bot_id = ?", (bot_id,)
        ).fetchone()
        return row[0] if row else None

    def acknowledge(self, bot_id: str, update_id: int) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO bot_offsets VALUES (?, ?) ON CONFLICT(bot_id)
                   DO UPDATE SET next_offset = MAX(next_offset, excluded.next_offset)""",
                (bot_id, update_id + 1),
            )
