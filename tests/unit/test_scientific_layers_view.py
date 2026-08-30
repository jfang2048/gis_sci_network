import pandas as pd

from gisnet.visualization.scientific_layers import scientific_layer_edge_view


def _edges() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "year": 2025,
                "corpus_view": "broad",
                "hierarchy_view": "organization",
                "source_id": source_id,
                "target_id": target_id,
                "source_name": source_name,
                "target_name": target_name,
                "source_region": source_region,
                "target_region": target_region,
                "source_country": source_country,
                "target_country": target_country,
                "source_category": "education",
                "target_category": target_category,
                "source_subregion": "Western Europe",
                "target_subregion": target_subregion,
                "value": value,
            }
            for (
                source_id,
                target_id,
                source_name,
                target_name,
                source_region,
                target_region,
                source_country,
                target_country,
                target_category,
                target_subregion,
                value,
            ) in (
                (
                    "I1",
                    "I2",
                    "Alpha",
                    "Beta",
                    "Europe",
                    "Asia",
                    "FR",
                    "JP",
                    "education",
                    "Eastern Asia",
                    0.8,
                ),
                (
                    "I1",
                    "I3",
                    "Alpha",
                    "Gamma",
                    "Europe",
                    "Americas",
                    "FR",
                    "US",
                    "facility",
                    "Northern America",
                    0.6,
                ),
                (
                    "I2",
                    "I1",
                    "Beta",
                    "Alpha",
                    "Asia",
                    "Europe",
                    "JP",
                    "FR",
                    "education",
                    "Western Europe",
                    0.4,
                ),
            )
        ]
    )


def test_directed_layer_view_keeps_endpoint_order_and_filters_focus() -> None:
    view = scientific_layer_edge_view(
        _edges(),
        year=2025,
        corpus_view="broad",
        hierarchy_view="organization",
        value_column="value",
        directed=True,
        limit=5,
        region_pair="Asia — Europe",
        country_code="FR",
    )

    assert view["edge_label"].tolist() == ["Alpha → Beta", "Beta → Alpha"]
    assert view["value"].tolist() == [0.8, 0.4]


def test_undirected_layer_view_uses_proximity_label_and_exact_minimum() -> None:
    view = scientific_layer_edge_view(
        _edges(),
        year=2025,
        corpus_view="broad",
        hierarchy_view="organization",
        value_column="value",
        directed=False,
        limit=1,
        institution_category="education",
        minimum_value=0.5,
    )

    assert view["edge_label"].tolist() == ["Alpha ↔ Beta"]
    assert view["value"].tolist() == [0.8]
