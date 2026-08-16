from gisnet.cli import main


def test_status_smoke(capsys: object) -> None:
    assert main(["status"]) == 0


def test_check_env_missing_key_is_safe(monkeypatch: object, capsys: object) -> None:
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)  # type: ignore[attr-defined]
    monkeypatch.delenv("openalex_api", raising=False)  # type: ignore[attr-defined]
    assert main(["check-env", "--offline"]) == 2
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "Set OPENALEX_API_KEY" in captured.err


def test_citation_flow_dry_run_names_the_layer_boundary(capsys: object) -> None:
    assert main(["build-citation-flows", "--dry-run"]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "directed citation-flow" in captured.out
    assert "not collaboration" in captured.out
