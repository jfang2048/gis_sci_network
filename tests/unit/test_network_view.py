from gisnet.visualization.network_view import EDGE_WIDTH_ENCODING, _accessibility_sentence


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
