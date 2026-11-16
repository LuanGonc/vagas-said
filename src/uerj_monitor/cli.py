"""Monitor local com HTML sintético e SQLite. Não consulta o portal nem envia mensagens."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from uerj_monitor.config import ConfigError, load_config
from uerj_monitor.models import SourcePhase
from uerj_monitor.monitor import LocalHtmlClient, Monitor
from uerj_monitor.parser import ParseError, parse_classes
from uerj_monitor.scheduler import run_monitor
from uerj_monitor.serialization import observation_to_dict
from uerj_monitor.storage import SQLiteStore


def _print_json(payload: object) -> None:
    # Saída ASCII mantém o JSON legível mesmo em terminais Windows sem UTF-8.
    print(json.dumps(payload, ensure_ascii=True), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    parse = commands.add_parser("parse", help="Ler arquivo HTML no contrato sintético.")
    parse.add_argument("html", type=Path)
    parse.add_argument("--discipline-code", required=True)
    parse.add_argument("--phase", choices=list(SourcePhase), default=SourcePhase.UNKNOWN)
    parse.add_argument("--encoding", default="utf-8", help="Codificação do HTML (padrão: utf-8).")
    monitor = commands.add_parser("monitor", help="Monitorar arquivos HTML locais com SQLite.")
    monitor.add_argument("--config", type=Path, required=True)
    monitor.add_argument(
        "--once", action="store_true", help="Executar uma única leitura e encerrar."
    )
    events = commands.add_parser("events", help="Listar os eventos locais mais recentes.")
    events.add_argument("--config", type=Path, required=True)
    events.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    if args.command != "parse":
        return _monitor_command(args)
    try:
        html = args.html.read_text(encoding=args.encoding)
        observations = parse_classes(
            html,
            discipline_code=args.discipline_code,
            source_phase=SourcePhase(args.phase),
        )
    except (OSError, UnicodeError, LookupError):
        print("Não foi possível ler o HTML; confira o arquivo e sua codificação.", file=sys.stderr)
        return 1
    except (ParseError, ValueError) as error:
        print(f"Falha na leitura estruturada: {error}", file=sys.stderr)
        return 1
    _print_json([observation_to_dict(observation) for observation in observations])
    return 0


def _monitor_command(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as error:
        print(f"Configuração inválida: {error}", file=sys.stderr)
        return 1
    try:
        with SQLiteStore(config.database) as store:
            if args.command == "events":
                _print_json([event.to_dict() for event in store.list_events(args.limit)])
                return 0
            monitor = Monitor(
                LocalHtmlClient(config.sources, config.encoding),
                store,
                config.watches,
                phase=config.phase,
            )
            if args.once:
                result = monitor.poll_once()
                _print_json(result.to_dict())
                return 1 if result.failures else 0
            code = run_monitor(
                monitor,
                config.scheduler,
                on_cycle=lambda result: _print_json(result.to_dict()),
            )
            if code == 2:
                print(
                    "Monitor encerrado após falhas consecutivas; revise a fonte local.",
                    file=sys.stderr,
                )
            return code
    except KeyboardInterrupt:
        print("Monitor encerrado pelo usuário.", file=sys.stderr)
        return 130
    except (sqlite3.Error, OSError, ValueError):
        print(
            "Não foi possível acessar o estado local; confira a configuração e o banco.",
            file=sys.stderr,
        )
        return 1
