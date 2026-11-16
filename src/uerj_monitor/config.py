"""Configuração TOML local, com caminhos relativos ao arquivo de configuração."""

import codecs
import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

from uerj_monitor.models import SourcePhase, Watch
from uerj_monitor.serialization import watch_key


class ConfigError(ValueError):
    """Configuração inválida; mensagens não reproduzem valores do arquivo."""


@dataclass(frozen=True)
class SchedulerSettings:
    interval_seconds: float = 60
    jitter_fraction: float = 0.1
    max_backoff_seconds: float = 900
    max_failures: int = 5

    def __post_init__(self) -> None:
        for value in (self.interval_seconds, self.jitter_fraction, self.max_backoff_seconds):
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ConfigError("Os intervalos e o jitter precisam ser números finitos.")
        if self.interval_seconds < 60:
            raise ConfigError("O intervalo mínimo nesta etapa é de 60 segundos.")
        if not 0 <= self.jitter_fraction <= 0.25:
            raise ConfigError("O jitter deve estar entre 0 e 0.25.")
        if self.max_backoff_seconds < self.interval_seconds:
            raise ConfigError("O limite de backoff deve ser maior ou igual ao intervalo.")
        if type(self.max_failures) is not int or self.max_failures < 1:
            raise ConfigError("O limite de falhas deve ser um inteiro positivo.")


@dataclass(frozen=True)
class MonitorConfig:
    database: Path
    sources: dict[str, Path]
    watches: tuple[Watch, ...]
    phase: SourcePhase
    encoding: str
    scheduler: SchedulerSettings


def _string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("Um campo de texto obrigatório está ausente ou inválido.")
    return value.strip()


def _keys(table: object, allowed: set[str]) -> dict:
    if not isinstance(table, dict) or set(table) - allowed:
        raise ConfigError("Seção inválida ou opção desconhecida na configuração.")
    return table


def _tables(value: object) -> list[dict]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ConfigError("Disciplinas e turmas precisam ser listas de tabelas TOML.")
    return value


def load_config(path: Path) -> MonitorConfig:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError("Não foi possível ler a configuração TOML.") from error
    _keys(raw, {"monitor", "disciplines", "watches"})
    options = _keys(
        raw.get("monitor", {}),
        {
            "database",
            "phase",
            "encoding",
            "interval_seconds",
            "jitter_fraction",
            "max_backoff_seconds",
            "max_failures",
        },
    )
    try:
        phase = SourcePhase(options.get("phase", "unknown"))
    except (ValueError, TypeError) as error:
        raise ConfigError("A fase deve ser unknown, SID ou SAID.") from error
    encoding = _string(options.get("encoding", "utf-8"))
    try:
        codecs.lookup(encoding)
    except LookupError as error:
        raise ConfigError("Codificação de HTML desconhecida.") from error
    scheduler = SchedulerSettings(
        **{
            key: options[key]
            for key in (
                "interval_seconds",
                "jitter_fraction",
                "max_backoff_seconds",
                "max_failures",
            )
            if key in options
        }
    )
    root = path.resolve().parent
    sources: dict[str, Path] = {}
    for entry in _tables(raw.get("disciplines", [])):
        _keys(entry, {"code", "html"})
        code = _string(entry.get("code"))
        if code in sources:
            raise ConfigError("Disciplina repetida na lista de fontes locais.")
        sources[code] = (root / _string(entry.get("html"))).resolve()
    watches = []
    seen = set()
    for entry in _tables(raw.get("watches", [])):
        _keys(
            entry, {"discipline_code", "class_number", "vacancy_type", "discipline_name", "enabled"}
        )
        enabled = entry.get("enabled", True)
        if type(enabled) is not bool:
            raise ConfigError("enabled deve ser true ou false.")
        name = entry.get("discipline_name")
        watch = Watch(
            discipline_code=_string(entry.get("discipline_code")),
            class_number=_string(entry.get("class_number")).upper(),
            vacancy_type=_string(entry.get("vacancy_type", "UERJ")).upper(),
            discipline_name=None if name is None else _string(name),
            enabled=enabled,
        )
        if watch_key(watch) in seen:
            raise ConfigError("Disciplina, turma e tipo de vaga repetidos na configuração.")
        seen.add(watch_key(watch))
        if enabled:
            if watch.discipline_code not in sources:
                raise ConfigError("Uma turma ativa não possui uma fonte HTML configurada.")
            watches.append(watch)
    if not watches:
        raise ConfigError("Configure pelo menos uma turma ativa.")
    return MonitorConfig(
        database=(root / _string(options.get("database", "data/monitor.sqlite3"))).resolve(),
        sources=sources,
        watches=tuple(watches),
        phase=phase,
        encoding=encoding,
        scheduler=scheduler,
    )
