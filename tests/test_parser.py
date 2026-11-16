from datetime import datetime

import pytest
from bs4 import BeautifulSoup

from uerj_monitor.models import ParseStatus, SourcePhase
from uerj_monitor.parser import ParseError, parse_classes


def test_two_classes_both_tables_and_distinct_vacancy_types(html, captured_at):
    observations = parse_classes(html, discipline_code="TEST001", captured_at=captured_at)
    assert len(observations) == 3
    first, external, second = observations
    assert first.watch.discipline_code == "TEST001"
    assert first.watch.class_number == "01"
    assert first.watch.vacancy_type == "UERJ"
    assert first.numbers.as_tuple() == (25, 25, 30, 18, 10)
    assert first.captured_at == captured_at
    assert first.source_phase == SourcePhase.UNKNOWN
    assert first.parse_status == ParseStatus.OK
    assert external.watch.class_number == "01"
    assert external.watch.vacancy_type == "EXTERNA"
    assert external.numbers.as_tuple() == (2, 0, 2, 0, 0)
    assert second.watch.class_number == "02"
    assert second.numbers.as_tuple() == (25, None, 25, 3, None)
    assert second.parse_status == ParseStatus.PARTIAL


def test_phase_is_supplied_explicitly_not_inferred(html):
    result = parse_classes(html, discipline_code="TEST001", source_phase=SourcePhase.SAID)
    assert all(item.source_phase == SourcePhase.SAID for item in result)
    assert all(item.captured_at.utcoffset() is not None for item in result)


@pytest.mark.parametrize("empty", ["", "-", "—", "&nbsp;"])
def test_missing_counts_remain_unknown(html, empty):
    result = parse_classes(html.replace("&nbsp;", empty), discipline_code="TEST001")
    assert result[-1].numbers.occupied is None


@pytest.mark.parametrize("invalid", ["vinte", "-1", "25 vagas", "1,5", "<script>x</script>"])
def test_invalid_number_fails_without_exposing_html(html, invalid):
    html = html.replace("<td>18</td>", f"<td>{invalid}</td>")
    with pytest.raises(ParseError) as error:
        parse_classes(html, discipline_code="TEST001")
    assert invalid not in str(error.value)


@pytest.mark.parametrize(
    "html",
    ["", "<html><form>Faça login novamente</form></html>", "<p>Serviço indisponível</p>"],
)
def test_unknown_or_login_html_is_not_a_successful_empty_result(html):
    with pytest.raises(ParseError):
        parse_classes(html, discipline_code="TEST001")


def test_missing_table_preserves_other_table_without_inventing_counts(html):
    soup = BeautifulSoup(html, "html.parser")
    soup.find("table").decompose()
    result = parse_classes(str(soup), discipline_code="TEST001")
    assert result[0].numbers.as_tuple() == (None, None, 30, 18, 10)
    assert result[0].parse_status == ParseStatus.PARTIAL


def test_reordering_tables_and_data_columns_does_not_change_association(html):
    soup = BeautifulSoup(html, "html.parser")
    first_section = soup.find("section")
    first_table = first_section.find("table")
    first_section.append(first_table.extract())
    for row in first_table.find_all("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        row.insert(0, cells[-1].extract())
    result = parse_classes(str(soup), discipline_code="TEST001")
    assert result[0].numbers.as_tuple() == (25, 25, 30, 18, 10)


@pytest.mark.parametrize(
    "old,new",
    [
        ("<th>Ocupadas</th>", "<th>Solicitadas</th>"),
        ("<td>18</td>", "<td>18</td><td>99</td>"),
        ('colspan="2"', 'colspan="0"'),
        ('rowspan="2"', 'rowspan="3"'),
        ("<td>18</td>", '<td colspan="2">18</td>'),
        ("<caption>Vagas Atualizadas da Turma</caption>", ""),
    ],
)
def test_unexpected_structure_fails_explicitly(html, old, new):
    with pytest.raises(ParseError):
        parse_classes(html.replace(old, new), discipline_code="TEST001")


def test_duplicate_vacancy_type_is_ambiguous(html):
    html = html.replace('<th scope="row">Externa</th>', '<th scope="row">UERJ</th>')
    with pytest.raises(ParseError):
        parse_classes(html, discipline_code="TEST001")


def test_duplicate_table_is_ambiguous(html):
    soup = BeautifulSoup(html, "html.parser")
    soup.find("section").append(BeautifulSoup(str(soup.find("table")), "html.parser"))
    with pytest.raises(ParseError):
        parse_classes(str(soup), discipline_code="TEST001")


def test_duplicate_class_block_is_ambiguous(html):
    html = html.replace("TURMA 02", "TURMA 01")
    with pytest.raises(ParseError):
        parse_classes(html, discipline_code="TEST001")


def test_naive_capture_time_is_rejected(html):
    with pytest.raises(ValueError, match="fuso"):
        parse_classes(html, discipline_code="TEST001", captured_at=datetime(2026, 11, 16))
