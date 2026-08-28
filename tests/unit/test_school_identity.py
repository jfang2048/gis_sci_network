import csv
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from gisnet.dataset import file_sha256
from gisnet.institutions.overrides import InstitutionOverrideRegistry
from gisnet.schools.identity import build_school_identities


def test_build_school_identities_preserves_organizations_and_exposes_uncertainty(
    tmp_path: Path,
) -> None:
    institutions_path = tmp_path / "institutions.parquet"
    candidates_path = tmp_path / "candidates.parquet"
    overrides_path = tmp_path / "overrides.csv"
    identities_path = tmp_path / "school_identities.parquet"
    audit_path = tmp_path / "school_identity_audit.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {"institution_id": "I1", "display_name": "Parent University"},
                {"institution_id": "I2", "display_name": "Verified Institute"},
                {"institution_id": "I3", "display_name": "Ambiguous Laboratory"},
            ]
        ),
        institutions_path,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "institution_id": "I1",
                    "openalex_lineage_ids": [],
                    "openalex_parent_ids": [],
                    "ror_parent_ids": [],
                    "ror_parent_institution_ids": [],
                    "candidate_umbrella_institution_ids": [],
                },
                {
                    "institution_id": "I2",
                    "openalex_lineage_ids": ["I1"],
                    "openalex_parent_ids": ["I1"],
                    "ror_parent_ids": ["https://ror.org/parent"],
                    "ror_parent_institution_ids": ["I1"],
                    "candidate_umbrella_institution_ids": ["I1"],
                },
                {
                    "institution_id": "I3",
                    "openalex_lineage_ids": ["I1"],
                    "openalex_parent_ids": ["I1"],
                    "ror_parent_ids": [],
                    "ror_parent_institution_ids": [],
                    "candidate_umbrella_institution_ids": ["I1"],
                },
            ]
        ),
        candidates_path,
    )
    with overrides_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "rule_id",
                "action",
                "source_institution_id",
                "target_institution_id",
                "country_code",
                "reason",
                "provenance",
            ]
        )
        writer.writerow(
            [
                "school-1",
                "collapse",
                "I2",
                "I1",
                "",
                "Verified parent relationship",
                "https://example.org/evidence",
            ]
        )
    source_hash = file_sha256(institutions_path)

    summary = build_school_identities(
        institutions_path,
        candidates_path,
        InstitutionOverrideRegistry.load(overrides_path),
        identities_path=identities_path,
        audit_path=audit_path,
    )

    identities = {row["institution_id"]: row for row in pq.read_table(identities_path).to_pylist()}
    assert identities["I1"]["canonical_school_id"] == "I1"
    assert identities["I2"]["canonical_school_id"] == "I1"
    assert identities["I2"]["identity_status"] == "explicit_evidence_collapse"
    assert identities["I3"]["canonical_school_id"] == "I3"
    assert identities["I3"]["identity_status"] == "unresolved_relationship_candidate"
    assert identities["I3"]["quality_flags"] == ["ambiguous_fragmentation"]
    audit = pq.read_table(audit_path).to_pylist()
    assert audit[0]["rule_id"] == "school-1"
    assert audit[0]["reversible"] is True
    assert summary["explicit_collapse_count"] == 1
    assert summary["unresolved_relationship_count"] == 1
    assert summary["organization_source_sha256_before"] == source_hash
    assert summary["organization_source_sha256_after"] == source_hash
