from pathlib import Path


def test_ci_and_local_gate_are_offline_and_do_not_reference_secrets() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    gate = Path("scripts/quality-gate.sh").read_text(encoding="utf-8")

    for required in (
        "uv run ruff check .",
        "uv run ruff format --check .",
        "uv run mypy",
        "uv run pytest",
        "uv run python -m gisnet.cli status",
    ):
        assert required in gate
    assert "permissions:\n  contents: read" in workflow
    assert "persist-credentials: false" in workflow
    assert "pull_request_target" not in workflow
    assert "secrets." not in workflow
    assert "OPENALEX_API_KEY" not in workflow
    assert "set -x" not in gate


def test_network_tests_are_declared_and_skipped_by_default() -> None:
    project = Path("pyproject.toml").read_text(encoding="utf-8")

    assert "-m 'not network'" in project
    assert '"network: tests that require external network access"' in project
