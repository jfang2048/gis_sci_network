import json
from pathlib import Path

from gisnet.reporting.data_dictionary import TABLES, build_public_data_dictionary


def test_every_public_table_column_has_dictionary_and_provenance(tmp_path: Path) -> None:
    dictionary = tmp_path / "dictionary.json"
    report = tmp_path / "dictionary.md"
    summary = build_public_data_dictionary(
        data_directory="dashboard/data",
        metadata_path="dashboard/data/metadata.json",
        output_json=dictionary,
        output_markdown=report,
    )
    payload = json.loads(dictionary.read_text(encoding="utf-8"))
    markdown = report.read_text(encoding="utf-8")

    assert summary["released_table_count"] == summary["documented_table_count"] == len(TABLES)
    assert summary["tables_with_primary_keys"] == len(TABLES)
    assert summary["tables_with_source_manifests"] == len(TABLES)
    assert summary["tables_with_known_issue_notes"] == len(TABLES)
    assert summary["private_path_or_key_count"] == 0
    assert (
        sum(table["column_count"] for table in payload["tables"]) == summary["column_entry_count"]
    )
    assert all(
        column["description"] and column["null_semantics"]
        for table in payload["tables"]
        for column in table["columns"]
    )
    assert all(table["source_manifest"] for table in payload["tables"])
    assert "/home/" not in markdown
    assert "OPENALEX_API_KEY=" not in markdown
