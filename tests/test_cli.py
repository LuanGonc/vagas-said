import json
from pathlib import Path

from uerj_monitor.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "turmas_sintetico.html"


def test_local_html_to_json(capsys):
    result = main(["parse", str(FIXTURE), "--discipline-code", "TEST001"])
    captured = capsys.readouterr()
    assert result == 0
    assert captured.err == ""
    observations = json.loads(captured.out)
    assert len(observations) == 3
    assert observations[0]["numbers"]["offered"] == 25
    assert observations[0]["numbers"]["requested_offered"] == 30
    assert observations[1]["numbers"]["occupied"] == 0
    assert observations[2]["numbers"]["occupied"] is None
    assert observations[2]["numbers"]["requested_preferential"] is None
    assert observations[0]["captured_at"].endswith("+00:00")


def test_missing_file_returns_error_without_traceback(tmp_path, capsys):
    assert main(["parse", str(tmp_path / "missing.html"), "--discipline-code", "TEST001"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Traceback" not in captured.err


def test_bad_html_returns_error_without_leaking_content(tmp_path, capsys):
    path = tmp_path / "login.html"
    path.write_text("<h1>dados pessoais de exemplo</h1>", encoding="utf-8")
    assert main(["parse", str(path), "--discipline-code", "TEST001"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "dados pessoais" not in captured.err


def test_unknown_encoding_returns_controlled_error(capsys):
    result = main(
        ["parse", str(FIXTURE), "--discipline-code", "TEST001", "--encoding", "invalid-encoding"]
    )
    assert result == 1
    assert "Traceback" not in capsys.readouterr().err


def local_config(tmp_path):
    html_path = tmp_path / "classes.html"
    html_path.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[monitor]\ndatabase = "state.sqlite3"\n'
        '[[disciplines]]\ncode = "TEST001"\nhtml = "classes.html"\n'
        '[[watches]]\ndiscipline_code = "TEST001"\nclass_number = "01"\n',
        encoding="utf-8",
    )
    return config_path, html_path


def test_monitor_once_and_events_survive_independent_cli_calls(tmp_path, capsys):
    config_path, html_path = local_config(tmp_path)
    command = ["monitor", "--config", str(config_path), "--once"]
    assert main(command) == 0
    first = json.loads(capsys.readouterr().out)
    assert first == {"observed": 1, "failures": [], "events": []}
    html_path.write_text(
        html_path.read_text(encoding="utf-8").replace("<td>18</td>", "<td>3</td>"),
        encoding="utf-8",
    )
    assert main(command) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["events"]) == 1
    assert result["events"][0]["kind"] == "numbers_changed"
    assert main(command) == 0
    assert json.loads(capsys.readouterr().out)["events"] == []
    assert main(["events", "--config", str(config_path)]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1


def test_monitor_missing_html_has_controlled_failure(tmp_path, capsys):
    config_path, _ = local_config(tmp_path)
    # Aponta para um caminho inexistente, preservando a fixture criada no teste.
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace("classes.html", "missing.html"),
        encoding="utf-8",
    )
    assert main(["monitor", "--config", str(config_path), "--once"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["events"] == []
    assert result["failures"][0]["reason"] == "source_error"


def test_missing_config_is_a_controlled_error(tmp_path, capsys):
    assert main(["monitor", "--config", str(tmp_path / "missing.toml"), "--once"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Traceback" not in captured.err


def test_keyboard_interrupt_closes_monitor_cleanly(tmp_path, capsys, monkeypatch):
    import uerj_monitor.cli as cli

    config_path, _ = local_config(tmp_path)

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_monitor", interrupt)
    assert main(["monitor", "--config", str(config_path)]) == 130
    assert "encerrado pelo usuário" in capsys.readouterr().err
