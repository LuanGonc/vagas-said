from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from uerj_monitor.discipline_parser import PORTAL_URL
from uerj_monitor.portal_client import ObservedPortalClient, PortalQueryError

ROOT = Path(__file__).parent / "fixtures"


def test_class_query_uses_current_list_fields_and_mapped_identifier():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200, text=(ROOT / "turmas_real_sanitizado.html").read_text(encoding="utf-8")
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as session:
        client = ObservedPortalClient(
            session, (ROOT / "disciplinas_real_sanitizado.html").read_text(encoding="utf-8")
        )
        assert "TURMA:" in client.fetch_discipline_classes("IME01-00000")
    assert len(requests) == 1
    assert str(requests[0].url) == PORTAL_URL
    assert parse_qs(requests[0].content.decode()) == {
        "_token": ["fixture-token"],
        "requisicao": ["fixture-action"],
        "matricula": ["000000000000"],
        "disciplinas[0]": ["1001"],
    }


@pytest.mark.parametrize("code", ["IME02-00000", "DESCONHECIDA"])
def test_unknown_or_unoffered_discipline_does_not_make_a_request(code):
    def handler(request):
        pytest.fail("Não deveria consultar uma disciplina ausente/sem oferta.")

    with httpx.Client(transport=httpx.MockTransport(handler)) as session:
        client = ObservedPortalClient(
            session, (ROOT / "disciplinas_real_sanitizado.html").read_text(encoding="utf-8")
        )
        with pytest.raises(PortalQueryError):
            client.fetch_discipline_classes(code)


@pytest.mark.parametrize(
    "status,body",
    [
        (200, "<form>Faça login novamente</form>"),
        (302, ""),
        (429, ""),
        (500, ""),
    ],
)
def test_expired_or_failed_response_is_not_an_empty_success(status, body):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, text=body, headers={"location": "https://example.invalid/"})

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as session:
        client = ObservedPortalClient(
            session, (ROOT / "disciplinas_real_sanitizado.html").read_text(encoding="utf-8")
        )
        with pytest.raises(PortalQueryError) as error:
            client.fetch_discipline_classes("IME01-00000")
    assert len(requests) == 1
    assert "fixture-token" not in str(error.value)
    assert "Faça login" not in str(error.value)
