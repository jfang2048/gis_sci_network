import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))


def test_dashboard_entrypoint_is_thin_and_uses_explicit_page_boundaries() -> None:
    source = (ROOT / "dashboard/app.py").read_text(encoding="utf-8")

    assert len(source.splitlines()) < 2_200
    assert "pd.read_parquet" not in source
    assert "query_school_profile(" not in source
    assert "query_school_profiles(" not in source
    assert "query_school_topics(" not in source
    assert "query_school_topics_for_schools(" not in source
    assert "query_school_ego_partners(" not in source
    assert 'view == "School Ego Map"' not in source
    assert "render_school_finder(" in source
    assert "render_school_profile(" in source
    assert "render_school_comparison(" in source


def test_dashboard_refactor_modules_are_explicit_and_importable() -> None:
    from dashboard_components import show_chart, show_data, style_figure
    from dashboard_data_access import load_metadata, load_table, require_table
    from school_compare_page import render_school_comparison
    from school_finder_page import render_school_finder
    from school_profile_page import render_school_profile

    assert callable(load_metadata)
    assert callable(load_table)
    assert callable(require_table)
    assert callable(show_chart)
    assert callable(show_data)
    assert callable(style_figure)
    assert callable(render_school_finder)
    assert callable(render_school_profile)
    assert callable(render_school_comparison)
