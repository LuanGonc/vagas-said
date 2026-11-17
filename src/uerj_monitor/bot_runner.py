"""Polling Telegram com offset durável e monitoramento local das assinaturas."""

import random
import time
from collections.abc import Callable

from uerj_monitor.bot import BotService
from uerj_monitor.bot_monitor import SubscriptionMonitor
from uerj_monitor.config import SchedulerSettings
from uerj_monitor.notifier import TelegramClient, TelegramError
from uerj_monitor.outbox import DeliveryWorker, Outbox
from uerj_monitor.storage import SQLiteStore


class BotRunner:
    def __init__(
        self,
        service: BotService,
        monitor: SubscriptionMonitor,
        client: TelegramClient,
        store: SQLiteStore,
        settings: SchedulerSettings,
        *,
        clock: Callable[[], float] = time.time,
        wait=time.sleep,
        report: Callable[[dict], None] = lambda result: None,
    ) -> None:
        self.service, self.monitor, self.client, self.store = service, monitor, client, store
        self.settings, self.clock, self.wait, self.report = settings, clock, wait, report
        self.bot_id = client.credentials.token.split(":", 1)[0]
        self.next_monitor_at = 0.0
        self.monitor_failures = 0
        self.reply_after = {}
        self.next_message_at = 0.0

    def run_once(self, timeout: int = 5) -> None:
        offset = self.service.repository.offset(self.bot_id)
        updates = self.client.get_updates(offset, timeout=timeout)
        updates = [
            update
            for update in updates
            if isinstance(update, dict) and type(update.get("update_id")) is int
        ]
        for update in sorted(updates, key=lambda item: item["update_id"]):
            offset = self.service.repository.offset(self.bot_id)
            if offset is not None and update["update_id"] < offset:
                continue
            interaction = self.service.handle(update)
            # As mutações são idempotentes. Persistir antes da resposta impede repetir
            # automaticamente uma resposta que pode ter chegado durante um timeout.
            self.service.repository.acknowledge(self.bot_id, update["update_id"])
            if interaction is None:
                continue
            if interaction.callback_id:
                try:
                    self.client.answer_callback(interaction.callback_id)
                except TelegramError as error:
                    self.report({"callback_error": error.code})
            delay = self.reply_after.get(interaction.user_id, 0) - self.clock()
            if delay > 3:
                # Não bloquear todos os usuários por uma limitação deste chat.
                self.report({"reply_deferred": True})
                continue
            if delay > 0:
                self.wait(delay)
            if self.next_message_at > self.clock():
                self.wait(self.next_message_at - self.clock())
            try:
                self.client.send_message(
                    interaction.reply.text,
                    chat_id=str(interaction.user_id),
                    reply_markup=interaction.reply.markup,
                )
                self.reply_after[interaction.user_id] = self.clock() + 3
            except TelegramError as error:
                self.report({"reply_error": error.code})
                if error.retryable:
                    self.reply_after[interaction.user_id] = self.clock() + max(
                        60, error.retry_after or 0
                    )
                # Uma resposta perdida pode ser consultada novamente com /minhas.
            self.next_message_at = self.clock() + 0.05
        if self.clock() >= self.next_monitor_at:
            result = self.monitor.poll_once()
            self.report({"monitor": result.to_dict()})
            self.monitor_failures = self.monitor_failures + 1 if result.failures else 0
            delay = min(
                self.settings.max_backoff_seconds,
                self.settings.interval_seconds * 2 ** min(self.monitor_failures, 20),
            )
            delay = min(
                self.settings.max_backoff_seconds,
                delay * (1 + self.settings.jitter_fraction * random.random()),
            )
            self.next_monitor_at = self.clock() + delay
        # Cada pessoa recebe apenas os eventos da sua assinatura.
        recipients = sorted({sub.user_id for sub in self.service.repository.list_active()})
        for user_id in recipients:
            if self.reply_after.get(user_id, 0) > self.clock():
                continue
            worker = DeliveryWorker(
                Outbox(self.store),
                self.client.for_chat(user_id),
                clock=self.clock,
                wait=self.wait,
                subscription_user_id=user_id,
            )
            if self.next_message_at > self.clock():
                self.wait(self.next_message_at - self.clock())
            report = worker.deliver_due(limit=1)
            self.report({"notifications": report})
            if report["sent"] or report["errors"]:
                self.next_message_at = self.clock() + 0.05

    def run(self) -> int:
        failures = 0
        while True:
            try:
                self.run_once()
                failures = 0
            except TelegramError as error:
                failures += 1
                self.report({"telegram_error": error.code})
                if failures >= self.settings.max_failures:
                    return 2
                remaining = max(error.retry_after or 0, min(900, 2**failures))
                while remaining > 0:
                    duration = min(remaining, 30)
                    self.wait(duration)
                    remaining -= duration
