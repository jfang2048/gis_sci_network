from pathlib import Path
from xml.etree import ElementTree

import pandas as pd  # type: ignore[import-untyped]

from gisnet.visualization.gallery import build_readme_gallery


def test_build_readme_gallery_writes_accessible_snapshot_figures(tmp_path: Path) -> None:
    nodes_path = tmp_path / "nodes.parquet"
    edges_path = tmp_path / "edges.parquet"
    topics_path = tmp_path / "topics.parquet"
    network_path = tmp_path / "network.svg"
    topic_path = tmp_path / "topics.svg"
    node_rows = [
        {
            "year": 2025,
            "corpus_view": "broad",
            "hierarchy_view": "organization",
            "institution_id": f"I{index}",
            "display_name": f"Institution {index}",
            "macro_region": region,
            "fractional_strength": float(10 - index),
            "core_rank": index,
            "x": float(index),
            "y": float(index % 2),
        }
        for index, region in enumerate(["Asia", "Europe", "Americas", "Africa", "Oceania"], start=1)
    ]
    edge_rows = [
        {
            "year": 2025,
            "corpus_view": "broad",
            "hierarchy_view": "organization",
            "source_id": "I1",
            "target_id": f"I{target}",
            "source_region": "Asia",
            "target_region": region,
            "fractional_count": float(10 - target),
        }
        for target, region in [(2, "Europe"), (3, "Americas"), (4, "Africa")]
    ]
    topic_rows = [
        {
            "year": 2025,
            "corpus_view": "broad",
            "hierarchy_view": "organization",
            "topic_family": f"topic_{index}",
            "fractional_count": float(20 - index),
        }
        for index in range(1, 9)
    ]
    pd.DataFrame(node_rows).to_parquet(nodes_path, index=False)
    pd.DataFrame(edge_rows).to_parquet(edges_path, index=False)
    pd.DataFrame(topic_rows).to_parquet(topics_path, index=False)

    summary = build_readme_gallery(
        network_nodes_path=nodes_path,
        network_edges_path=edges_path,
        topics_path=topics_path,
        network_figure_path=network_path,
        topic_figure_path=topic_path,
    )

    assert summary["year"] == 2025
    assert summary["network_node_count"] == 5
    assert summary["network_edge_count"] == 3
    assert summary["topic_family_count"] == 8
    for path, title_id, description_id in [
        (network_path, "network-title", "network-description"),
        (topic_path, "topic-title", "topic-description"),
    ]:
        root = ElementTree.parse(path).getroot()
        assert root.attrib["role"] == "img"
        assert root.attrib["viewBox"] == "0 0 1200 720"
        assert root.attrib["aria-labelledby"] == f"{title_id} {description_id}"
        ids = {node.attrib.get("id") for node in root.iter()}
        assert title_id in ids
        assert description_id in ids
