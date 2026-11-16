from threading import Event

import pytest

from uerj_monitor.config import SchedulerSettings
from uerj_monitor.monitor import CycleResult, PollFailure
from uerj_monitor.scheduler import run_monitor

SUCCESS = CycleResult(1, (), ())
FAILURE = CycleResult(0, (PollFailure("TEST001", "source_error"),), ())


class ScriptedMonitor:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = 0

    def poll_once(self):
        self.calls += 1
        return next(self.results)


def test_backoff_recovers_to_normal_without_real_sleep():
    monitor = ScriptedMonitor([FAILURE, FAILURE, SUCCESS, SUCCESS])
    delays, received = [], []

    def wait(delay):
        delays.append(delay)
        return len(delays) == 4

    code = run_monitor(
        monitor, SchedulerSettings(jitter_fraction=0), wait=wait, on_cycle=received.append
    )
    assert code == 0
    assert delays == [120, 240, 60, 60]
    assert len(received) == 4


def test_jitter_and_backoff_cap():
    delays = []
    monitor = ScriptedMonitor([SUCCESS, FAILURE, FAILURE])

    def wait(delay):
        delays.append(delay)
        return len(delays) == 3

    run_monitor(
        monitor,
        SchedulerSettings(max_backoff_seconds=200),
        wait=wait,
        random_value=lambda: 0.5,
    )
    assert delays == pytest.approx([63, 126, 200])


def test_repeated_failures_stop_before_another_wait():
    monitor = ScriptedMonitor([FAILURE, FAILURE])
    delays = []
    code = run_monitor(
        monitor,
        SchedulerSettings(max_failures=2, jitter_fraction=0),
        wait=lambda delay: delays.append(delay) or False,
    )
    assert code == 2
    assert monitor.calls == 2
    assert delays == [120]


def test_stop_signal_skips_sleep_and_next_poll():
    stop = Event()
    monitor = ScriptedMonitor([SUCCESS])
    delays = []
    code = run_monitor(
        monitor,
        SchedulerSettings(),
        stop=stop,
        wait=lambda delay: delays.append(delay) or False,
        on_cycle=lambda result: stop.set(),
    )
    assert code == 0
    assert monitor.calls == 1
    assert delays == []


def test_already_stopped_monitor_does_not_poll():
    stop = Event()
    stop.set()
    monitor = ScriptedMonitor([])
    assert run_monitor(monitor, SchedulerSettings(), stop=stop) == 0
    assert monitor.calls == 0
