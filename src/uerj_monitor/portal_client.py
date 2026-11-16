"""Contrato de acesso futuro. Não há endpoint ou autenticação implementados."""

from typing import Protocol


class PortalClient(Protocol):
    def fetch_discipline_classes(self, discipline_code: str) -> str:
        """Retorna HTML em uma sessão autorizada, ou lança erro de consulta.

        Implementar somente após validar o fluxo descrito em docs/protocolo.md.
        Falhas de sessão/rede nunca devem retornar HTML vazio como sucesso.
        """
        ...
