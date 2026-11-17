"""Monitoramento por assinatura; contexto de consulta reservado para cada usuário."""

from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime

from uerj_monitor.catalog import LocalCatalog
from uerj_monitor.monitor import CycleResult, PollFailure, SourceError
from uerj_monitor.notifier import TelegramCredentials
from uerj_monitor.parser import ParseError
from uerj_monitor.serialization import watch_key
from uerj_monitor.storage import SQLiteStore
from uerj_monitor.subscriptions import SubscriptionRepository


class SubscriptionMonitor:
    def __init__(
        self,
        store: SQLiteStore,
        catalog: LocalCatalog,
        credentials: TelegramCredentials,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self.store, self.catalog, self.credentials, self.clock = store, catalog, credentials, clock
        self.repository = SubscriptionRepository(store)

    def poll_once(self) -> CycleResult:
        groups = defaultdict(list)
        for subscription in self.repository.list_active():
            groups[subscription.user_id, subscription.watch.discipline_code].append(subscription)
        events, failures, observed = [], [], 0
        for (user_id, code), subscriptions in groups.items():
            try:
                observations = self.catalog.observe(code, user_id, self.clock())
            except (SourceError, ParseError, OSError, ValueError):
                failures.append(PollFailure(code, "source_error"))
                continue
            by_key = {watch_key(item.watch): item for item in observations}
            if any(watch_key(sub.watch) not in by_key for sub in subscriptions):
                failures.append(PollFailure(code, "missing_watch"))
                continue
            destination = replace(self.credentials, chat_id=str(user_id)).destination
            for sub in subscriptions:
                result = self.store.record(
                    replace(by_key[watch_key(sub.watch)], watch=sub.watch),
                    subscription_id=sub.id,
                    delivery_destination=destination,
                )
                observed += 1
                if result.event is not None:
                    events.append(result.event)
        return CycleResult(observed, tuple(failures), tuple(events))
