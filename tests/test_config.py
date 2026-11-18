from pathlib import Path

import pytest

from uerj_monitor.config import ConfigError, SchedulerSettings, load_config
from uerj_monitor.models import SourcePhase

EXAMPLE = Path(__file__).parents[1] / "config.example.toml"


def write_config(tmp_path, content):
    path = tmp_path / "config.toml"
    path.write_text(content, encoding="utf-8")
    return path


def test_example_config_has_sources_relative_to_config_not_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config(EXAMPLE)
    assert config.sources["TEST001"].is_file()
    assert config.database == EXAMPLE.parent / "data" / "monitor.sqlite3"
    assert [watch.class_number for watch in config.watches] == ["01", "02"]
    assert config.phase == SourcePhase.UNKNOWN
    assert config.scheduler.interval_seconds == 60


@pytest.mark.parametrize(
    "old,new",
    [
        ("interval_seconds = 60", "interval_seconds = 30"),
        ("interval_seconds = 60", "interval_seconds = true"),
        ("interval_seconds = 60", "interval_seconds = nan"),
        ("jitter_fraction = 0.1", "jitter_fraction = -0.1"),
        ("max_backoff_seconds = 900", "max_backoff_seconds = 20"),
        ("max_failures = 5", "max_failures = 0"),
        ("max_failures = 5", "max_failures = 1.5"),
        ('phase = "unknown"', 'phase = "inventada"'),
        ('encoding = "utf-8"', 'encoding = "não-existe"'),
        ('code = "TEST001"', 'code = "SEM_FONTE"'),
        ('class_number = "01"', "class_number = 1"),
        ('class_number = "02"', 'class_number = "01"'),
        ('class_number = "02"', 'class_number = "02"\nenabled = "sim"'),
        ("interval_seconds = 60", "intervalo = 60"),
    ],
)
def test_invalid_configs_fail_without_echoing_values(tmp_path, old, new):
    content = EXAMPLE.read_text(encoding="utf-8").replace(old, new, 1)
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, content))


def test_disabled_watch_is_excluded(tmp_path):
    content = EXAMPLE.read_text(encoding="utf-8").replace(
        'class_number = "02"', 'class_number = "02"\nenabled = false'
    )
    config = load_config(write_config(tmp_path, content))
    assert len(config.watches) == 1


@pytest.mark.parametrize("content", ["", "[monitor", "watches = 1"])
def test_empty_malformed_or_wrong_structure_is_rejected(tmp_path, content):
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, content))


def test_monitor_settings_validate_direct_construction():
    with pytest.raises(ConfigError):
        SchedulerSettings(interval_seconds=float("inf"))


def test_telegram_configuration_has_no_credentials_and_resolves_env_path(tmp_path):
    content = EXAMPLE.read_text(encoding="utf-8").replace(
        'env_file = ".env"', 'env_file = "private/.env"'
    )
    config = load_config(write_config(tmp_path, content))
    assert config.telegram.enabled is False
    assert config.telegram.env_file == tmp_path / "private" / ".env"


def test_telegram_enabled_must_be_boolean(tmp_path):
    content = EXAMPLE.read_text(encoding="utf-8").replace("enabled = false", 'enabled = "sim"', 1)
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, content))


@pytest.mark.parametrize("admins", ["[true]", "[0]", '["1"]', '"1"'])
def test_bot_admins_require_positive_integer_ids(tmp_path, admins):
    content = EXAMPLE.read_text(encoding="utf-8") + f"\n[bot]\nadmin_user_ids = {admins}\n"
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, content))


def test_central_bot_config_discovers_catalog_without_local_sources(tmp_path):
    example = EXAMPLE.parent / "config.bot.central.example.toml"
    config = load_config(example, require_watches=False)
    assert config.portal.enabled is True
    assert config.portal.env_file is None
    assert config.portal.recover_once is True
    assert config.sources == {}
    assert config.watches == ()
    content = example.read_text(encoding="utf-8").replace(
        '# env_file = ".env"', 'env_file = "private/.env"'
    )
    config = load_config(write_config(tmp_path, content), require_watches=False)
    assert config.portal.env_file == tmp_path / "private" / ".env"


@pytest.mark.parametrize(
    "portal", ['enabled = "sim"', "enabled = 1", 'recover_once = "sim"', 'env_file = ""']
)
def test_central_options_are_strict(tmp_path, portal):
    content = EXAMPLE.read_text(encoding="utf-8") + f"\n[portal]\n{portal}\n"
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, content))
