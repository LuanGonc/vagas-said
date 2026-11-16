"""Polling sequencial com jitter, backoff e interrupção controlada."""

import random
from collections.abc import Callable
from threading import Event

from uerj_monitor.config import SchedulerSettings
from uerj_monitor.monitor import CycleResult, Monitor


def run_monitor(
    monitor: Monitor,
    settings: SchedulerSettings,
    *,
    stop: Event | None = None,
    wait: Callable[[float], bool] | None = None,
    random_value: Callable[[], float] = random.random,
    on_cycle: Callable[[CycleResult], None] = lambda result: None,
) -> int:
    """Retorna 0 ao parar, 2 após falhas consecutivas. Não chama o portal diretamente.

    wait retorna True para encerrar. Relógio, espera e jitter podem ser controlados
    nos testes; nenhuma espera real é necessária para verificar as transições.
    """
    stop = stop if stop is not None else Event()
    pause = wait if wait is not None else stop.wait
    failures = 0
    while not stop.is_set():
        result = monitor.poll_once()
        on_cycle(result)
        failures = failures + 1 if result.failures else 0
        if failures >= settings.max_failures:
            return 2
        base = min(
            settings.max_backoff_seconds,
            settings.interval_seconds * 2 ** min(failures, 30),
        )
        delay = min(
            settings.max_backoff_seconds,
            base * (1 + settings.jitter_fraction * random_value()),
        )
        if stop.is_set() or pause(delay):
            return 0
    return 0
