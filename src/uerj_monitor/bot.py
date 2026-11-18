"""Conversa privada: buscar disciplina, mapear turmas e gerenciar dez assinaturas."""

import time
from dataclasses import dataclass

from uerj_monitor.catalog import Catalog
from uerj_monitor.monitor import SourceError
from uerj_monitor.parser import ParseError
from uerj_monitor.subscriptions import SubscribeResult, SubscriptionRepository

PAGE_SIZE = 6


@dataclass(frozen=True)
class Reply:
    text: str
    markup: dict | None = None


@dataclass(frozen=True)
class Interaction:
    user_id: int
    reply: Reply
    callback_id: str | None = None


class BotService:
    def __init__(
        self,
        repository: SubscriptionRepository,
        catalog: Catalog,
        clock=time.time,
        admin_user_ids: tuple[int, ...] = (),
    ):
        self.repository, self.catalog, self.clock = repository, catalog, clock
        self.admin_user_ids = frozenset(admin_user_ids)

    def _button(self, user_id: int, title: str, payload: dict) -> list[dict]:
        return [
            {
                "text": title[:80],
                "callback_data": self.repository.action(user_id, payload, self.clock()),
            }
        ]

    def handle(self, update: dict) -> Interaction | None:
        callback = update.get("callback_query")
        callback_id = None
        if isinstance(callback, dict):
            actor, message = callback.get("from"), callback.get("message")
            callback_id = callback.get("id")
        else:
            message = update.get("message")
            actor = message.get("from") if isinstance(message, dict) else None
        if not isinstance(actor, dict) or not isinstance(message, dict):
            return None
        user_id, chat = actor.get("id"), message.get("chat")
        if (
            type(user_id) is not int
            or user_id <= 0
            or actor.get("is_bot")
            or not isinstance(chat, dict)
            or chat.get("type") != "private"
            or chat.get("id") != user_id
        ):
            return None
        text = message.get("text", "") if not isinstance(callback, dict) else ""
        if isinstance(text, str) and text.strip().split("@", 1)[0] == "/id":
            return Interaction(user_id, Reply(f"Seu ID no Telegram: {user_id}"))
        if user_id in self.admin_user_ids:
            self.repository.grant(user_id, user_id)
        if not self.repository.authorized(user_id):
            return Interaction(
                user_id,
                Reply(f"O responsável precisa autorizar seu acesso. Seu ID: {user_id}."),
                callback_id if isinstance(callback_id, str) else None,
            )
        try:
            if isinstance(callback, dict):
                data = callback.get("data")
                action = (
                    self.repository.resolve_action(user_id, data, self.clock())
                    if (isinstance(data, str) and data.startswith("a:") and len(data) <= 64)
                    else None
                )
                reply = (
                    self._action(user_id, action)
                    if action
                    else Reply(
                        "Este botão expirou ou não pertence à sua conversa. Use /buscar ou /minhas."
                    )
                )
            else:
                text = message.get("text")
                if not isinstance(text, str):
                    return None
                reply = self._command(user_id, text.strip())
        except PermissionError:
            reply = Reply("O responsável precisa autorizar seu acesso.")
        except (SourceError, ParseError, OSError, ValueError):
            reply = Reply("Não consegui consultar as turmas agora. Tente novamente mais tarde.")
        return Interaction(user_id, reply, callback_id if isinstance(callback_id, str) else None)

    def _command(self, user_id: int, text: str) -> Reply:
        command, _, argument = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        if command in {"/autorizar", "/revogar"}:
            if user_id not in self.admin_user_ids:
                return Reply("Somente o responsável pode gerenciar o acesso.")
            if not argument.isascii() or not argument.isdecimal() or not 0 < int(argument) < 2**63:
                return Reply(f"Use {command} seguido do ID numérico da pessoa.")
            target = int(argument)
            if command == "/autorizar":
                self.repository.grant(target, user_id)
                return Reply("Acesso autorizado. A pessoa já pode buscar suas disciplinas.")
            if target in self.admin_user_ids:
                return Reply("Administradores são definidos na configuração do responsável.")
            self.repository.revoke(target)
            return Reply("Acesso revogado e monitoramentos da pessoa desativados.")
        if command in {"/start", "/ajuda"}:
            return Reply(
                self._label("Monitor UERJ") + "\n"
                "Busque uma disciplina pelo código ou nome e escolha as turmas nos botões.\n"
                "Você pode acompanhar até 10 turmas ao todo.\n"
                "/buscar Cálculo II — buscar disciplinas\n"
                "/minhas — ver suas turmas\n"
                "/parar — escolher uma turma para remover\n"
                "O monitoramento continua após os alertas, até você remover a turma."
            )
        if command in {"/minhas", "/parar"}:
            if command == "/parar" and argument:
                if not argument.isascii() or not argument.isdecimal():
                    return Reply("Use /parar para escolher uma das suas turmas.")
                return self._remove(user_id, int(argument))
            return self._subscriptions(user_id)
        if command == "/turmas":
            return self._classes(user_id, argument.strip())
        if command == "/buscar":
            return self._search(user_id, argument)
        if command.startswith("/"):
            return Reply("Use /buscar, /minhas ou /parar. Para ajuda, envie /ajuda.")
        return self._search(user_id, text)

    def _label(self, text: str) -> str:
        return f"Demonstração local — {text}" if self.catalog.is_demo else text

    def _search(self, user_id: int, query: str, page: int = 0) -> Reply:
        query = query.strip()[:150]
        matches = self.catalog.search(query, user_id)
        if not matches:
            return Reply("Nenhuma disciplina encontrada no catálogo. Tente outro código ou nome.")
        start = page * PAGE_SIZE
        rows = [
            self._button(
                user_id,
                f"{item.code} — {item.name}",
                {
                    "kind": "classes",
                    "code": item.code,
                },
            )
            for item in matches[start : start + PAGE_SIZE]
        ]
        if start + PAGE_SIZE < len(matches):
            rows.append(
                self._button(
                    user_id,
                    "Mais disciplinas",
                    {
                        "kind": "search",
                        "query": query,
                        "page": page + 1,
                    },
                )
            )
        return Reply(self._label("Escolha uma disciplina:"), {"inline_keyboard": rows})

    def _classes(self, user_id: int, code: str, page: int = 0) -> Reply:
        classes = self.catalog.classes(code, user_id)
        start = page * PAGE_SIZE
        rows = [
            self._button(
                user_id,
                f"Monitorar turma {watch.class_number}",
                {
                    "kind": "add",
                    "code": code,
                    "class_number": watch.class_number,
                },
            )
            for watch in classes[start : start + PAGE_SIZE]
        ]
        if start + PAGE_SIZE < len(classes):
            rows.append(
                self._button(
                    user_id,
                    "Mais turmas",
                    {
                        "kind": "classes",
                        "code": code,
                        "page": page + 1,
                    },
                )
            )
        if not rows:
            return Reply("Não há turmas UERJ para mostrar nesta consulta.")
        return Reply(
            self._label(code) + "\nEscolha as turmas que quer acompanhar:",
            {"inline_keyboard": rows},
        )

    def _subscriptions(self, user_id: int) -> Reply:
        subscriptions = self.repository.list_active(user_id)
        lines = [f"Suas turmas: {len(subscriptions)}/10"]
        rows = []
        for item in subscriptions:
            watch = item.watch
            lines.append(
                f"{watch.discipline_code} — {(watch.discipline_name or '')[:80]}"
                f" | Turma {watch.class_number}"
            )
            rows.append(
                self._button(
                    user_id,
                    f"Remover {watch.discipline_code} / turma {watch.class_number}",
                    {"kind": "remove", "id": item.id},
                )
            )
        if not subscriptions:
            lines.append("Use /buscar para escolher sua primeira disciplina.")
        return Reply("\n".join(lines), {"inline_keyboard": rows} if rows else None)

    def _remove(self, user_id: int, subscription_id: int) -> Reply:
        removed = self.repository.unsubscribe(user_id, subscription_id)
        return Reply(
            "Turma removida. Você liberou uma posição para outra turma."
            if removed
            else "Esta turma não está ativa na sua lista. Use /minhas."
        )

    def _action(self, user_id: int, action: dict) -> Reply:
        match action["kind"]:
            case "search":
                return self._search(user_id, action["query"], action.get("page", 0))
            case "classes":
                return self._classes(user_id, action["code"], action.get("page", 0))
            case "remove":
                return self._remove(user_id, action["id"])
            case "add":
                classes = self.catalog.classes(action["code"], user_id)
                selected = next(
                    (watch for watch in classes if watch.class_number == action["class_number"]),
                    None,
                )
                if selected is None:
                    return Reply(
                        "Esta turma não aparece mais na consulta. Busque a disciplina novamente."
                    )
                result = self.repository.subscribe(user_id, selected)
                if result == SubscribeResult.LIMIT_REACHED:
                    return Reply(
                        "Você atingiu o limite de 10 turmas. Use /parar para liberar uma posição."
                    )
                if result == SubscribeResult.ALREADY_ACTIVE:
                    return Reply("Você já acompanha esta turma. Ela ocupa apenas uma posição.")
                count = len(self.repository.list_active(user_id))
                return Reply(
                    f"Turma {selected.class_number} adicionada: {selected.discipline_code}.\n"
                    f"Você acompanha {count}/10 turmas. Use /minhas para gerenciar."
                )
            case _:
                return Reply("Este botão não está disponível. Use /buscar ou /minhas.")
