from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gisnet.dataset import file_sha256
from gisnet.network.continuity import build_community_continuity, match_community_sets


def test_synthetic_split_merge_birth_and_disappearance() -> None:
    split = match_community_sets(
        {"A": {"1", "2", "3", "4"}, "GONE": {"9"}},
        {"C": {"1", "2"}, "D": {"3", "4"}, "BORN": {"10"}},
    )
    assert split.previous_overlap_degree["A"] == 2
    assert split.unmatched_current == ("BORN", "D")
    assert split.unmatched_previous == ("GONE",)
    assert split.assignments == {"C": "A"}

    merge = match_community_sets(
        {"A": {"1", "2"}, "B": {"3", "4"}},
        {"C": {"1", "2", "3", "4"}},
    )
    assert merge.current_overlap_degree["C"] == 2
    assert merge.assignments == {"C": "A"}
    assert merge.unmatched_previous == ("B",)


def test_low_overlap_is_uncertain_and_unchanged_output_is_stable(tmp_path: Path) -> None:
    source = tmp_path / "communities.parquet"
    rows = []
    for year, community, members in (
        (2010, "A", ("n1", "n2", "n3", "n4")),
        (2011, "B", ("n1", "n5", "n6", "n7")),
        (2012, "C", ("n1", "n5", "n6", "n7")),
    ):
        rows.extend(
            {
                "year": year,
                "corpus_view": "broad",
                "hierarchy_view": "organization",
                "institution_id": member,
                "community_id": community,
            }
            for member in members
        )
    pq.write_table(pa.Table.from_pylist(rows), source)
    hashes = []
    for suffix in ("one", "two"):
        continuity = tmp_path / f"continuity-{suffix}.parquet"
        transitions = tmp_path / f"transitions-{suffix}.parquet"
        summary = build_community_continuity(
            source,
            continuity_output=continuity,
            transitions_output=transitions,
        )
        hashes.append((file_sha256(continuity), file_sha256(transitions)))
        assert summary["uncertain_match_count"] == 1
        uncertain = duckdb.sql(
            "SELECT count(*) FROM read_parquet(?) WHERE low_overlap_uncertain",
            params=[str(continuity)],
        ).fetchone()
        assert uncertain == (1,)
    assert hashes[0] == hashes[1]
