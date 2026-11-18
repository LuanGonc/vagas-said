"""Monitor de HTML local com SQLite e integração opcional ao Telegram."""

import argparse
import getpass
import json
import sqlite3
import sys
import time
import warnings
from contextlib import ExitStack
from pathlib import Path

from uerj_monitor.bot import BotService
from uerj_monitor.bot_monitor import SubscriptionMonitor
from uerj_monitor.bot_runner import BotRunner
from uerj_monitor.catalog import LocalCatalog
from uerj_monitor.config import ConfigError, load_config
from uerj_monitor.models import SourcePhase
from uerj_monitor.monitor import LocalHtmlClient, Monitor
from uerj_monitor.notifier import TelegramClient, TelegramError, load_credentials
from uerj_monitor.outbox import DeliveryWorker, Outbox
from uerj_monitor.parser import ParseError, parse_classes
from uerj_monitor.portal_client import PortalQueryError
from uerj_monitor.portal_session import (
    AuthenticatedPortalSession,
    PortalCredentialError,
    PortalCredentials,
    load_portal_credentials,
)
from uerj_monitor.scheduler import run_monitor
from uerj_monitor.serialization import observation_to_dict
from uerj_monitor.storage import SQLiteStore
from uerj_monitor.subscriptions import SubscriptionRepository


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
    portal = commands.add_parser(
        "portal", help="Testar login UERJ local, sem monitoramento em loop."
    )
    portal_commands = portal.add_subparsers(dest="action", required=True)
    check = portal_commands.add_parser(
        "check", help="Autenticar e validar acesso ao catálogo real."
    )
    check.add_argument(
        "--env-file", type=Path, help="Ler credenciais de um arquivo local explícito."
    )
    check.add_argument("--discipline-code", help="Consultar também as turmas desta disciplina.")
    check.add_argument(
        "--recover-once",
        action="store_true",
        help="Permitir uma reautenticação por expiração reconhecida.",
    )
    for name, description in (
        ("bot", "Executar o bot privado com autorização e dez turmas por pessoa."),
        ("bot-demo", "Demonstrar o fluxo local sem token nem acesso à rede."),
    ):
        bot = commands.add_parser(name, help=description)
        bot.add_argument("--config", type=Path, required=True)
        if name == "bot":
            bot.add_argument("--once", action="store_true")
    telegram = commands.add_parser("telegram", help="Configurar e testar notificações Telegram.")
    actions = telegram.add_subparsers(dest="action", required=True)
    for name, help_text in (
        ("chats", "Listar IDs de chats sem expor mensagens ou nomes."),
        ("test", "Enviar uma mensagem de teste ao chat configurado."),
        ("queue", "Consultar a fila local sem acessar a rede."),
        ("flush", "Entregar um lote de notificações pendentes."),
        ("retry", "Recolocar uma entrega em falha na fila; pode repetir mensagem."),
    ):
        action = actions.add_parser(name, help=help_text)
        if name not in {"chats", "test"}:
            action.add_argument("--config", type=Path, required=True)
        if name != "queue":
            action.add_argument("--env-file", type=Path)
        if name == "queue":
            action.add_argument("--limit", type=int, default=100)
        if name == "retry":
            action.add_argument("--event-id", type=int, required=True)
    args = parser.parse_args(argv)
    if args.command == "portal":
        return _portal_command(args)
    if args.command in {"bot", "bot-demo"}:
        return _bot_command(args)
    if args.command == "telegram":
        return _telegram_command(args)
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
        with ExitStack() as stack:
            store = stack.enter_context(SQLiteStore(config.database))
            if args.command == "events":
                _print_json([event.to_dict() for event in store.list_events(args.limit)])
                return 0
            worker = None
            destination = None
            if config.telegram.enabled:
                credentials = load_credentials(config.telegram.env_file)
                destination = credentials.destination
                client = stack.enter_context(TelegramClient(credentials))
                worker = DeliveryWorker(Outbox(store), client)
            monitor = Monitor(
                LocalHtmlClient(config.sources, config.encoding),
                store,
                config.watches,
                phase=config.phase,
                delivery_destination=destination,
            )

            def on_cycle(result):
                payload = result.to_dict()
                if worker is not None:
                    payload["notifications"] = worker.deliver_due()
                _print_json(payload)
                return payload.get("notifications", {}).get("errors", 0)

            if args.once:
                result = monitor.poll_once()
                errors = on_cycle(result)
                return 1 if result.failures or errors else 0
            code = run_monitor(
                monitor,
                config.scheduler,
                on_cycle=on_cycle,
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
    except ConfigError as error:
        print(f"Configuração inválida: {error}", file=sys.stderr)
        return 1
    except (sqlite3.Error, OSError, ValueError):
        print(
            "Não foi possível acessar o estado local; confira a configuração e o banco.",
            file=sys.stderr,
        )
        return 1


def _telegram_command(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config) if hasattr(args, "config") else None
        with ExitStack() as stack:
            if args.action == "queue":
                store = stack.enter_context(SQLiteStore(config.database))
                _print_json(Outbox(store).list_deliveries(args.limit))
                return 0
            default_env = config.telegram.env_file if config else Path(".env")
            credentials = load_credentials(
                args.env_file or default_env, require_chat=args.action != "chats"
            )
            if args.action == "retry":
                store = stack.enter_context(SQLiteStore(config.database))
                queued = Outbox(store).retry(args.event_id, credentials.destination, time.time())
                _print_json({"requeued": queued, "event_id": args.event_id})
                return 0 if queued else 1
            client = stack.enter_context(TelegramClient(credentials))
            if args.action == "chats":
                _print_json(client.discover_chats())
            elif args.action == "test":
                message_id = client.send_message(
                    "Teste do Monitor de Vagas UERJ. A integração Telegram está funcionando."
                )
                _print_json({"sent": True, "message_id": message_id})
            else:
                store = stack.enter_context(SQLiteStore(config.database))
                result = DeliveryWorker(Outbox(store), client).deliver_due()
                _print_json(result)
                return 1 if result["errors"] else 0
        return 0
    except (ConfigError, TelegramError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Operação Telegram interrompida.", file=sys.stderr)
        return 130
    except (OSError, sqlite3.Error, ValueError):
        print("Falha ao acessar a configuração ou fila local do Telegram.", file=sys.stderr)
        return 1


def _bot_command(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config, require_watches=False)
        catalog = LocalCatalog(config)
        with SQLiteStore(":memory:" if args.command == "bot-demo" else config.database) as store:
            repository = SubscriptionRepository(store)
            service = BotService(repository, catalog, admin_user_ids=config.admin_user_ids)
            if args.command == "bot-demo":
                service = BotService(repository, catalog, admin_user_ids=(1,))
                update = {
                    "message": {
                        "from": {"id": 1},
                        "chat": {"id": 1, "type": "private"},
                        "text": "/buscar",
                    }
                }
                interaction = service.handle(update)
                _print_json({"demo": interaction.reply.text})
                for _ in range(2):
                    button = interaction.reply.markup["inline_keyboard"][0][0]
                    interaction = service.handle(
                        {
                            "callback_query": {
                                "from": {"id": 1},
                                "id": "demo",
                                "data": button["callback_data"],
                                "message": {"chat": {"id": 1, "type": "private"}},
                            }
                        }
                    )
                    _print_json({"demo": interaction.reply.text})
                update["message"]["text"] = "/minhas"
                _print_json({"demo": service.handle(update).reply.text})
                return 0
            credentials = load_credentials(
                config.telegram.env_file, require_chat=False, ignore_chat=True
            )
            with TelegramClient(credentials) as client:
                runner = BotRunner(
                    service,
                    SubscriptionMonitor(store, catalog, credentials),
                    client,
                    store,
                    config.scheduler,
                    report=_print_json,
                )
                if args.once:
                    runner.run_once(timeout=0)
                    return 0
                return runner.run()
    except (ConfigError, TelegramError) as error:
        print(str(error), file=sys.stderr)
        return 1

    except KeyboardInterrupt:
        print("Bot encerrado pelo usuário.", file=sys.stderr)
        return 130
    except (sqlite3.Error, OSError, ValueError):
        print(
            "Não foi possível executar o bot; confira o catálogo e o banco local.", file=sys.stderr
        )
        return 1


def _portal_command(args: argparse.Namespace) -> int:
    try:
        if args.env_file is not None:
            credentials = load_portal_credentials(args.env_file)
        else:
            if not sys.stdin.isatty():
                print(
                    "Execute em um terminal interativo ou informe --env-file com um arquivo local.",
                    file=sys.stderr,
                )
                return 1
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                registration = getpass.getpass("Matrícula UERJ (oculta): ").strip()
                password = getpass.getpass("Senha UERJ (oculta): ")
            credentials = PortalCredentials(registration, password)
        with AuthenticatedPortalSession(credentials, recover_once=args.recover_once) as session:
            session.login()
            disciplines = session.disciplines
            result = {
                "authenticated": True,
                "state": session.state.value,
                "disciplines": len(disciplines),
                "with_classes": sum(item.has_classes for item in disciplines),
            }
            if args.discipline_code:
                html = session.fetch_discipline_classes(args.discipline_code)
                observations = parse_classes(html, discipline_code=args.discipline_code)
                result["class_numbers"] = sorted({item.watch.class_number for item in observations})
                result["observations"] = len(observations)
            _print_json(result)
        return 0
    except (PortalQueryError, PortalCredentialError, ParseError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except getpass.GetPassWarning:
        print(
            "Este terminal não permite entrada oculta. Use outro terminal ou --env-file.",
            file=sys.stderr,
        )
        return 1
    except (KeyboardInterrupt, EOFError):
        print("Teste de login interrompido.", file=sys.stderr)
        return 130
    except (OSError, ValueError):
        print("Não foi possível concluir o teste local do portal.", file=sys.stderr)
        return 1
