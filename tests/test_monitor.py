from datetime import timedelta

import pytest

from uerj_monitor.models import SourcePhase, Watch
from uerj_monitor.monitor import LocalHtmlClient, Monitor, SourceError
from uerj_monitor.storage import EventKind, SQLiteStore

WATCH = Watch("TEST001", "01")


class SimulatedRule:
    """Regra fictícia de teste; não comprova elegibilidade no Aluno Online."""

    policy_id = "simulation-occupancy-v1"

    def evaluate(self, observation):
        offered, occupied = observation.numbers.offered, observation.numbers.occupied
        if offered is None or occupied is None:
            return None
        return occupied < offered


class MutableClient:
    def __init__(self, html):
        self.html = html
        self.error = None
        self.calls = []

    def fetch_discipline_classes(self, discipline_code):
        self.calls.append(discipline_code)
        if self.error is not None:
            raise self.error
        return self.html


class Clock:
    def __init__(self, start):
        self.value = start

    def __call__(self):
        self.value += timedelta(minutes=1)
        return self.value


def with_occupancy(html, occupied):
    return html.replace("<td>25</td><td>25</td>", f"<td>25</td><td>{occupied}</td>", 1)


def test_two_simulated_availability_events_including_restart(html, captured_at, tmp_path):
    path = tmp_path / "monitor.sqlite3"
    clock = Clock(captured_at)
    client = MutableClient(html)
    emitted = []
    with SQLiteStore(path) as store:
        monitor = Monitor(client, store, (WATCH,), rule=SimulatedRule(), clock=clock)
        for occupied in (25, 24, 24):
            client.html = with_occupancy(html, occupied)
            result = monitor.poll_once()
            assert result.failures == ()
            emitted.extend(result.events)
        assert len(store.list_events()) == 1
    with SQLiteStore(path) as store:
        monitor = Monitor(client, store, (WATCH,), rule=SimulatedRule(), clock=clock)
        for occupied in (25, 24):
            client.html = with_occupancy(html, occupied)
            emitted.extend(monitor.poll_once().events)
        assert len(store.list_events()) == 2
    assert len(emitted) == 2
    assert all(event.kind == EventKind.AVAILABILITY_DETECTED for event in emitted)


def test_one_fetch_per_discipline_and_only_selected_types(html, captured_at):
    client = MutableClient(html)
    watches = (WATCH, Watch("TEST001", "02"), Watch("IGNORED", "01", enabled=False))
    with SQLiteStore(":memory:") as store:
        result = Monitor(client, store, watches, clock=Clock(captured_at)).poll_once()
        assert result.observed == 2
        assert result.events == ()
        assert client.calls == ["TEST001"]
        assert store.load_latest(Watch("TEST001", "01", "EXTERNA")) is None


@pytest.mark.parametrize("error", [SourceError("privado"), TimeoutError("privado")])
def test_read_failures_preserve_baseline_and_never_emit_events(html, captured_at, error):
    client = MutableClient(html)
    with SQLiteStore(":memory:") as store:
        monitor = Monitor(client, store, (WATCH,), rule=SimulatedRule(), clock=Clock(captured_at))
        monitor.poll_once()
        original = store.load_latest(WATCH)
        client.error = error
        result = monitor.poll_once()
        assert result.events == ()
        assert result.failures[0].reason == "source_error"
        assert "privado" not in str(result.to_dict())
        assert store.load_latest(WATCH) == original
        assert store.list_events() == []
        client.error = None
        client.html = with_occupancy(html, 24)
        assert len(monitor.poll_once().events) == 1


def test_invalid_html_preserves_state(html, captured_at):
    client = MutableClient(html)
    with SQLiteStore(":memory:") as store:
        monitor = Monitor(client, store, (WATCH,), clock=Clock(captured_at))
        monitor.poll_once()
        original = store.load_latest(WATCH)
        client.html = "<form>login com dados privados</form>"
        result = monitor.poll_once()
        assert result.events == ()
        assert result.failures[0].reason == "parse_error"
        assert store.load_latest(WATCH) == original


def test_missing_watch_does_not_record_a_partial_discipline(html, captured_at):
    watches = (WATCH, Watch("TEST001", "99"))
    with SQLiteStore(":memory:") as store:
        result = Monitor(MutableClient(html), store, watches, clock=Clock(captured_at)).poll_once()
        assert result.failures[0].reason == "missing_watch"
        assert result.observed == 0
        assert store.load_latest(WATCH) is None


def test_default_monitor_records_numbers_not_availability(html, captured_at):
    client = MutableClient(html)
    with SQLiteStore(":memory:") as store:
        monitor = Monitor(client, store, (WATCH,), phase=SourcePhase.SAID, clock=Clock(captured_at))
        assert monitor.poll_once().events == ()
        client.html = html.replace("<td>18</td>", "<td>3</td>")
        events = monitor.poll_once().events
        assert len(events) == 1
        assert events[0].kind == EventKind.NUMBERS_CHANGED
        assert events[0].current.numbers.occupied == 25
        assert monitor.poll_once().events == ()


def test_available_first_capture_is_only_baseline(html, captured_at):
    client = MutableClient(with_occupancy(html, 24))
    with SQLiteStore(":memory:") as store:
        monitor = Monitor(client, store, (WATCH,), rule=SimulatedRule(), clock=Clock(captured_at))
        assert monitor.poll_once().events == ()
        assert monitor.poll_once().events == ()


def test_local_client_reads_explicit_encoding_and_wraps_errors(tmp_path):
    path = tmp_path / "local.html"
    path.write_bytes("Solicitação".encode("cp1252"))
    assert LocalHtmlClient({"TEST001": path}, "cp1252").fetch_discipline_classes("TEST001") == (
        "Solicitação"
    )
    with pytest.raises(SourceError):
        LocalHtmlClient({"TEST001": path}).fetch_discipline_classes("TEST001")
    with pytest.raises(SourceError):
        LocalHtmlClient({}).fetch_discipline_classes("TEST001")
