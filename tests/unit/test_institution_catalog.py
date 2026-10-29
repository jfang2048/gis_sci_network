"""The institution catalogue must never inherit a visualization Top-N limit."""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))

from school_finder_page import CATALOG_COLUMNS, institution_catalog  # noqa: E402


def test_complete_catalog_matches_every_checked_in_school_id() -> None:
    index = pd.read_parquet(ROOT / "dashboard/data/school_index.parquet")
    catalog = institution_catalog(index)
    csv_bytes = catalog.to_csv(index=False, lineterminator="\n").encode("utf-8")
    exported = pd.read_csv(BytesIO(csv_bytes), dtype={"school_id": "string"})

    assert list(catalog.columns) == CATALOG_COLUMNS
    assert len(catalog) == len(index) == len(exported)
    assert set(exported["school_id"]) == set(index["school_id"])
    assert catalog["school_id"].is_unique
    assert set(index.loc[~index["in_prior_visualization_core"], "school_id"]) <= set(
        exported["school_id"]
    )
    assert csv_bytes == institution_catalog(index.sample(frac=1, random_state=7)).to_csv(
        index=False, lineterminator="\n"
    ).encode("utf-8")


def test_catalogue_filters_do_not_change_full_index() -> None:
    index = pd.read_parquet(ROOT / "dashboard/data/school_index.parquet")
    europe = index.loc[index["macro_region"] == "Europe"]

    assert set(institution_catalog(europe)["school_id"]) == set(europe["school_id"])
    assert len(institution_catalog(index)) == len(index)


def test_catalogue_rejects_missing_or_duplicate_ids() -> None:
    index = pd.read_parquet(ROOT / "dashboard/data/school_index.parquet").head(2)

    with pytest.raises(ValueError, match="lacks catalogue columns"):
        institution_catalog(index.drop(columns=["openalex_id"]))
    with pytest.raises(ValueError, match="unique, non-null"):
        institution_catalog(pd.concat([index, index.iloc[[0]]], ignore_index=True))
