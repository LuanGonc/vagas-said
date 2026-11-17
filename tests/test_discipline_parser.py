from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from uerj_monitor.discipline_parser import parse_discipline_list, parse_navigation_fields
from uerj_monitor.parser import ParseError


@pytest.fixture
def html():
    return (Path(__file__).parent / "fixtures/disciplinas_real_sanitizado.html").read_text(
        encoding="utf-8"
    )


def test_list_maps_codes_names_ids_and_offer_without_academic_history(html):
    rows = parse_discipline_list(html)
    assert [(row.code, row.name, row.internal_id, row.has_classes) for row in rows] == [
        ("IME01-00000", "Disciplina de teste 1", "1001", True),
        ("IME02-00000", "Disciplina de teste 2", "1002", False),
        ("IME03-00000", "Disciplina de teste 3", "1003", True),
    ]
    assert set(rows[0].__dataclass_fields__) == {"code", "name", "internal_id", "has_classes"}


def test_private_fields_are_used_for_query_but_hidden_from_repr(html):
    fields = parse_navigation_fields(html)
    assert fields.class_query("1001") == {
        "_token": "fixture-token",
        "requisicao": "fixture-action",
        "matricula": "000000000000",
        "disciplinas[0]": "1001",
    }
    assert "fixture-token" not in repr(fields)
    assert "000000000000" not in repr(fields)


@pytest.mark.parametrize("damage", ["header", "id", "offer", "duplicate"])
def test_ambiguous_or_unexpected_list_is_not_silently_accepted(html, damage):
    soup = BeautifulSoup(html, "html.parser")
    if damage == "header":
        soup.find("th").string = "Outro campo"
    elif damage == "id":
        soup.find("a")["onclick"] = "javascript:outraAcao(output, 1001); return false;"
    elif damage == "offer":
        soup.find_all("tr")[1].find_all("td")[-1].string = "Talvez"
    else:
        for span in soup.find_all("span"):
            span.string = "IME01-00000 Disciplina repetida"
    with pytest.raises(ParseError):
        parse_discipline_list(str(soup))


@pytest.mark.parametrize("damage", ["target", "method", "empty", "duplicate"])
def test_navigation_form_rejects_unsafe_or_unknown_structure(html, damage):
    soup = BeautifulSoup(html, "html.parser")
    if damage == "target":
        soup.form["action"] = "https://example.invalid/"
    elif damage == "method":
        soup.form["method"] = "get"
    elif damage == "empty":
        soup.find("input")["value"] = ""
    else:
        soup.form.append(
            soup.new_tag("input", type="hidden", attrs={"name": "_token", "value": "x"})
        )
    with pytest.raises(ParseError):
        parse_navigation_fields(str(soup))
