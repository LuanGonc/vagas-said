from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from uerj_monitor.models import ParseStatus, SourcePhase
from uerj_monitor.parser import ParseError, parse_classes

FIXTURE = Path(__file__).parent / "fixtures" / "turmas_real_sanitizado.html"


@pytest.fixture
def html():
    return FIXTURE.read_text(encoding="utf-8")


def test_observed_portal_structure_maps_four_classes_both_tables_and_types(html, captured_at):
    observations = parse_classes(html, discipline_code="IME01-00000", captured_at=captured_at)
    assert len(observations) == 8
    uerj = [item for item in observations if item.watch.vacancy_type == "UERJ"]
    assert [item.watch.class_number for item in uerj] == ["1", "2", "3", "4"]
    assert [item.numbers.as_tuple() for item in uerj] == [
        (25, 25, 30, 18, 10),
        (25, 18, 25, 3, None),
        (40, None, 40, 20, 2),
        (30, 30, 30, 30, 20),
    ]
    assert uerj[1].parse_status == ParseStatus.PARTIAL
    assert uerj[2].numbers.occupied is None
    assert all(item.source_phase == SourcePhase.UNKNOWN for item in observations)
    assert all(item.captured_at == captured_at for item in observations)
    vestibular = [item for item in observations if item.watch.vacancy_type == "VESTIBULAR"]
    assert all(item.numbers.as_tuple() == (0, 0, 0, 0, 0) for item in vestibular)


def test_wrong_discipline_is_rejected_without_exposing_html(html):
    with pytest.raises(ParseError) as error:
        parse_classes(html, discipline_code="IME01-99999")
    assert "não corresponde" in str(error.value)
    assert "IME01-00000" not in str(error.value)


def test_classes_are_associated_by_own_row_not_visual_or_global_order(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="turmas-sanitizadas")
    rows = table.find_all("tr", recursive=False)
    for row in reversed(rows):
        table.append(row.extract())
    result = parse_classes(str(soup), discipline_code="IME01-00000")
    uerj = {
        item.watch.class_number: item.numbers.as_tuple()
        for item in result
        if item.watch.vacancy_type == "UERJ"
    }
    assert uerj["1"] == (25, 25, 30, 18, 10)
    assert uerj["4"] == (30, 30, 30, 30, 20)


def test_missing_table_is_partial_and_does_not_borrow_counts_from_next_class(html):
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find("table", id="turmas-sanitizadas").find_all("table")
    tables[0].decompose()
    result = parse_classes(str(soup), discipline_code="IME01-00000")
    assert result[0].numbers.as_tuple() == (None, None, 30, 18, 10)
    assert result[0].parse_status == ParseStatus.PARTIAL
    assert result[2].numbers.as_tuple() == (25, 18, 25, 3, None)


@pytest.mark.parametrize("damage", ["header", "number", "duplicate", "caption", "missing_label"])
def test_damaged_portal_structure_fails_explicitly(html, damage):
    soup = BeautifulSoup(html, "html.parser")
    if damage == "duplicate":
        labels = [tag for tag in soup.find_all("b") if tag.get_text(strip=True) == "TURMA:"]
        parent = labels[1].parent
        parent.clear()
        bold = soup.new_tag("b")
        bold.string = "TURMA:"
        parent.append(bold)
        parent.append(" 1")
        with pytest.raises(ParseError):
            parse_classes(str(soup), discipline_code="IME01-00000")
        return
    table = soup.find("table", id="turmas-sanitizadas").find("table")
    if damage == "header":
        table.find_all("tr")[0].find_all("td")[2].string = "Campo desconhecido"
    elif damage == "number":
        table.find_all("tr")[1].find_all("td")[2].string = "erro privado"
    elif damage == "caption":
        table.find("caption").insert(0, "Título desconhecido")
    else:
        labels = [tag for tag in soup.find_all("b") if tag.get_text(strip=True) == "TURMA:"]
        labels[0].parent.string = "TURMA:"
    with pytest.raises(ParseError):
        parse_classes(str(soup), discipline_code="IME01-00000")


def test_fixture_has_no_session_or_identifiable_header(html):
    soup = BeautifulSoup(html, "html.parser")
    assert not soup.find_all(["input", "script", "form", "a", "img"])
    assert "PHPSESSID" not in html
    assert "_token" not in html
    assert "matricula" not in html.casefold()
