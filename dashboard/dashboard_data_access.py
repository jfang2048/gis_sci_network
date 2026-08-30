"""Cached access to the checked-in public dashboard snapshot."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st


@st.cache_data(show_spinner=False)
def load_metadata(data_directory: Path) -> dict[str, object]:
    """Load public metadata without making any network request."""
    path = data_directory / "metadata.json"
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


@st.cache_resource(show_spinner=False)
def load_table(data_directory: Path, name: str) -> pd.DataFrame:
    """Load one compact public table once per dashboard process."""
    path = data_directory / f"{name}.parquet"
    if not path.is_file():
        return pd.DataFrame()
    return pd.read_parquet(path)


def require_table(
    data_directory: Path,
    name: str,
    *,
    columns: set[str] | None = None,
) -> pd.DataFrame:
    """Load a required snapshot table and stop on an incomplete contract."""
    frame = load_table(data_directory, name)
    if frame.empty:
        st.error(
            f"The dashboard snapshot is incomplete: `{name}.parquet` is missing or empty. "
            "Rebuild the processed dashboard bundle."
        )
        st.stop()
    missing = sorted((columns or set()).difference(frame.columns))
    if missing:
        st.error(
            f"The dashboard snapshot is incompatible: `{name}.parquet` lacks "
            f"{', '.join(missing)}. Rebuild the processed dashboard bundle."
        )
        st.stop()
    return frame
