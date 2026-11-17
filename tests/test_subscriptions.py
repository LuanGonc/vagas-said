from concurrent.futures import ThreadPoolExecutor

import pytest

from uerj_monitor.models import Watch
from uerj_monitor.storage import SQLiteStore
from uerj_monitor.subscriptions import SubscribeResult, SubscriptionRepository


def test_ten_total_per_user_and_duplicates_do_not_consume_a_slot():
    with SQLiteStore(":memory:") as store:
        repository = SubscriptionRepository(store)
        repository.grant(1, 1)
        repository.grant(2, 1)
        for index in range(10):
            assert repository.subscribe(1, Watch(f"D{index}", "01")) == SubscribeResult.ADDED
        assert repository.subscribe(1, Watch("D0", "01")) == SubscribeResult.ALREADY_ACTIVE
        assert repository.subscribe(1, Watch("D11", "01")) == SubscribeResult.LIMIT_REACHED
        assert repository.subscribe(2, Watch("D11", "01")) == SubscribeResult.ADDED
        item = repository.list_active(1)[0]
        assert repository.unsubscribe(2, item.id) is False
        assert repository.unsubscribe(1, item.id) is True
        assert repository.subscribe(1, Watch("D11", "01")) == SubscribeResult.ADDED
        assert len(repository.list_active(1)) == 10


def test_readding_removed_class_uses_a_new_subscription_and_baseline():
    with SQLiteStore(":memory:") as store:
        repository = SubscriptionRepository(store)
        repository.grant(1, 1)
        repository.subscribe(1, Watch("D1", "01"))
        original = repository.list_active(1)[0]
        repository.unsubscribe(1, original.id)
        repository.subscribe(1, original.watch)
        assert repository.list_active(1)[0].id != original.id


def test_limit_is_atomic_with_competing_database_connections(tmp_path):
    path = tmp_path / "bot.sqlite3"
    with SQLiteStore(path) as store:
        repository = SubscriptionRepository(store)
        repository.grant(1, 1)
        for index in range(9):
            repository.subscribe(1, Watch(f"D{index}", "01"))

    def subscribe(code):
        with SQLiteStore(path) as store:
            return SubscriptionRepository(store).subscribe(1, Watch(code, "01"))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(subscribe, ["D10", "D11"]))
    assert sorted(results) == sorted([SubscribeResult.ADDED, SubscribeResult.LIMIT_REACHED])
    with SQLiteStore(path) as store:
        assert len(SubscriptionRepository(store).list_active(1)) == 10


def test_access_is_required_and_revocation_disables_subscriptions():
    with SQLiteStore(":memory:") as store:
        repository = SubscriptionRepository(store)
        with pytest.raises(PermissionError):
            repository.subscribe(2, Watch("D1", "01"))
        repository.grant(2, 1)
        repository.subscribe(2, Watch("D1", "01"))
        repository.revoke(2)
        assert not repository.authorized(2)
        assert repository.list_active(2) == []
        repository.grant(2, 1)
        assert repository.list_active(2) == []


def test_actions_and_offsets_are_scoped_and_persisted(tmp_path):
    path = tmp_path / "bot.sqlite3"
    with SQLiteStore(path) as store:
        repository = SubscriptionRepository(store)
        token = repository.action(1, {"kind": "classes", "code": "D1"}, now=1000)
        assert len(token.encode()) <= 64
        assert repository.resolve_action(2, token, now=1001) is None
        repository.acknowledge("bot-a", 7)
        repository.acknowledge("bot-a", 3)
    with SQLiteStore(path) as store:
        repository = SubscriptionRepository(store)
        assert repository.resolve_action(1, token, now=1001)["code"] == "D1"
        assert repository.resolve_action(1, token, now=1900) is None
        assert repository.offset("bot-a") == 8
        assert repository.offset("bot-b") is None
