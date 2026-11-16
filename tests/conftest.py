from datetime import UTC, datetime
from pathlib import Path

import pytest


@pytest.fixture
def html() -> str:
    return (Path(__file__).parent / "fixtures" / "turmas_sintetico.html").read_text(
        encoding="utf-8"
    )


@pytest.fixture
def captured_at() -> datetime:
    return datetime(2026, 11, 16, 18, 0, tzinfo=UTC)
