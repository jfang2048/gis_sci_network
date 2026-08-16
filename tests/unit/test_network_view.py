from gisnet.visualization.network_view import (
    EDGE_WIDTH_ENCODING,
    _accessibility_sentence,
    visible_accessibility_sentence,
)


def test_accessibility_summary_states_encodings_and_threshold() -> None:
    text = _accessibility_sentence(
        {
            "year": 2025,
            "corpus_view": "broad",
            "hierarchy_view": "organization",
            "node_count": 100,
            "edge_count": 200,
            "cross_region_edge_count": 50,
            "top_institution": "Example University",
            "visible_minimum_fractional_weight": 0.125,
        }
    )
    assert "2025" in text
    assert "broad" in text
    assert "50 edges cross" in text
    assert "0.125" in text
    assert "constant display width" in text
    assert "fractional weight controls inclusion" in text
    assert EDGE_WIDTH_ENCODING == "constant; selected weight controls inclusion only"


def test_visible_accessibility_summary_uses_filtered_counts_and_selected_weight() -> None:
    text = visible_accessibility_sentence(
        year=2025,
        corpus_view="broad",
        hierarchy_view="organization",
        node_count=125,
        edge_count=37,
        cross_region_edge_count=9,
        counting_method="Full",
        minimum_weight=2.0,
        size_metric="degree",
        color_metric="macro-region",
    )
    assert "125 visible core institutions" in text
    assert "37 visible edges" in text
    assert "9 cross macro-regions" in text
    assert "full weight controls inclusion" in text
    assert "minimum visible full edge weight is 2" in text
    assert "node size encodes degree" in text
    assert "node color encodes macro-region" in text
