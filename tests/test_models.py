from datetime import datetime

import pytest

from uerj_monitor.models import ClassObservation, VacancyNumbers, Watch


@pytest.mark.parametrize("value", [-1, 1.5, True, "25"])
def test_counts_reject_invalid_values(value):
    with pytest.raises(ValueError):
        VacancyNumbers(occupied=value)


def test_capture_requires_timezone():
    with pytest.raises(ValueError, match="fuso"):
        ClassObservation(Watch("TEST001", "01"), datetime(2026, 11, 16), VacancyNumbers())


@pytest.mark.parametrize("discipline,turma,tipo", [("", "01", "UERJ"), ("X", " ", "UERJ")])
def test_watch_requires_nonempty_identity(discipline, turma, tipo):
    with pytest.raises(ValueError):
        Watch(discipline, turma, tipo)
