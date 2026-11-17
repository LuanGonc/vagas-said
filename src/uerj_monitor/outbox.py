"""Entrega serial com fila durável; confirmações ambíguas exigem repetição explícita."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from uerj_monitor.notifier import TelegramClient, TelegramError, format_event
from uerj_monitor.storage import EventKind, SQLiteStore, StoredEvent, _decode


@dataclass(frozen=True)
class Delivery:
    event: StoredEvent
    attempts: int
    destination: str


class Outbox:
    def __init__(self, store: SQLiteStore) -> None:
        self.connection = store.connection

    def claim(
        self, destination: str, now: float, *, subscription_user_id: int | None = None
    ) -> Delivery | None:
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            if self.connection.execute(
                "SELECT 1 FROM deliveries WHERE destination = ? AND status = 'sending' LIMIT 1",
                (destination,),
            ).fetchone():
                return None
            cooldown = self.connection.execute(
                "SELECT not_before FROM delivery_limits WHERE destination = ?", (destination,)
            ).fetchone()
            if cooldown and cooldown[0] > now:
                return None
            query = """SELECT events.*, deliveries.attempts FROM deliveries
                   JOIN events ON events.id = deliveries.event_id
                   WHERE destination = ? AND status = 'pending' AND available_at <= ?
                   """
            parameters = [destination, now]
            if subscription_user_id is not None:
                query += """AND EXISTS (SELECT 1 FROM subscription_events se
                             JOIN subscriptions s ON s.id = se.subscription_id
                             JOIN bot_access a ON a.user_id = s.user_id
                             WHERE se.event_id = events.id AND s.user_id = ? AND s.active = 1) """
                parameters.append(subscription_user_id)
            row = self.connection.execute(
                query + "ORDER BY event_id LIMIT 1", parameters
            ).fetchone()
            if row is None:
                return None
            event = StoredEvent(
                row["id"],
                EventKind(row["kind"]),
                _decode(row["previous"]),
                _decode(row["current"]),
                row["policy_id"],
            )
            self.connection.execute(
                """UPDATE deliveries SET status = 'sending', attempts = attempts + 1
                   WHERE event_id = ?""",
                (event.id,),
            )
            return Delivery(event, row["attempts"] + 1, destination)

    def complete(self, delivery: Delivery, message_id: int, now: float) -> None:
        with self.connection:
            cursor = self.connection.execute(
                """UPDATE deliveries SET status = 'sent', message_id = ?, sent_at = ?,
                   last_error = NULL WHERE event_id = ? AND status = 'sending'""",
                (message_id, now, delivery.event.id),
            )
            if cursor.rowcount != 1:
                raise ValueError("A entrega não está mais em andamento.")
            self.connection.execute(
                """INSERT INTO delivery_limits (destination, not_before) VALUES (?, ?)
                   ON CONFLICT(destination) DO UPDATE SET
                     not_before = MAX(not_before, excluded.not_before)""",
                (delivery.destination, now + 3),
            )

    def fail(self, delivery: Delivery, error: TelegramError, destination: str, now: float) -> None:
        delay = max(min(900, 60 * 2 ** (delivery.attempts - 1)), error.retry_after or 0)
        status = "failed"
        if error.uncertain:
            status = "uncertain"
        elif error.retryable and delivery.attempts < 3:
            status = "pending"
        with self.connection:
            self.connection.execute(
                """UPDATE deliveries SET status = ?, available_at = ?, last_error = ?
                   WHERE event_id = ? AND status = 'sending'""",
                (status, now + delay, error.code, delivery.event.id),
            )
            if error.retryable:
                # O cooldown também impede que o evento seguinte ignore um HTTP 429.
                self.connection.execute(
                    """INSERT INTO delivery_limits (destination, not_before) VALUES (?, ?)
                       ON CONFLICT(destination) DO UPDATE SET
                         not_before = MAX(not_before, excluded.not_before)""",
                    (destination, now + delay),
                )

    def list_deliveries(self, limit: int = 100) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("O limite deve estar entre 1 e 1000.")
        rows = self.connection.execute(
            """SELECT event_id, status, attempts, last_error, message_id, sent_at, available_at
               FROM deliveries ORDER BY event_id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["sent_at"] = (
                datetime.fromtimestamp(row["sent_at"], UTC).isoformat()
                if row["sent_at"] is not None
                else None
            )
            item["available_at"] = datetime.fromtimestamp(row["available_at"], UTC).isoformat()
            result.append(item)
        return result

    def retry(self, event_id: int, destination: str, now: float) -> bool:
        """Ação manual: pode repetir mensagem já recebida. Pare o monitor antes de usar."""
        with self.connection:
            cursor = self.connection.execute(
                """UPDATE deliveries SET status = 'pending', attempts = 0, available_at = ?,
                   last_error = NULL WHERE event_id = ? AND destination = ?
                   AND status IN ('failed', 'uncertain', 'sending')""",
                (now, event_id, destination),
            )
            return cursor.rowcount == 1


class DeliveryWorker:
    def __init__(
        self,
        outbox: Outbox,
        client: TelegramClient,
        *,
        clock: Callable[[], float] = time.time,
        wait: Callable[[float], None] = time.sleep,
        subscription_user_id: int | None = None,
    ) -> None:
        self.outbox, self.client = outbox, client
        self.clock, self.wait = clock, wait
        self.subscription_user_id = subscription_user_id

    def deliver_due(self, limit: int = 10) -> dict:
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("Um lote de entrega deve conter de 1 a 10 mensagens.")
        sent, errors = 0, 0
        destination = self.client.credentials.destination
        for index in range(limit):
            if index:
                # Três segundos atendem também ao limite conservador de grupos.
                self.wait(3)
            delivery = self.outbox.claim(
                destination, self.clock(), subscription_user_id=self.subscription_user_id
            )
            if delivery is None:
                break
            try:
                message_id = self.client.send_message(format_event(delivery.event))
            except TelegramError as error:
                self.outbox.fail(delivery, error, destination, self.clock())
                errors += 1
                break
            except ValueError:
                self.outbox.fail(
                    delivery, TelegramError("invalid_message"), destination, self.clock()
                )
                errors += 1
                break
            except BaseException:
                self.outbox.fail(
                    delivery,
                    TelegramError("interrupted", uncertain=True),
                    destination,
                    self.clock(),
                )
                raise
            self.outbox.complete(delivery, message_id, self.clock())
            sent += 1
        return {"sent": sent, "errors": errors}
