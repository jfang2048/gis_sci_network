import pandas as pd

from gisnet.visualization.dashboard_filters import (
    CONTROL_APPLICABILITY,
    control_is_enabled,
    dimension_options,
    filter_geographic_view,
    local_collaboration_profile,
    partner_share_view,
)


def test_control_applicability_is_page_aware() -> None:
    assert control_is_enabled("Geographic Flows", "Country")
    assert not control_is_enabled("Geographic Flows", "Year")
    assert control_is_enabled("Institutional Network", "Consortium policy")
    assert control_is_enabled("School Profile", "Corpus view")
    assert not control_is_enabled("School Profile", "Year")
    assert control_is_enabled("Compare Schools", "Corpus view")
    assert not control_is_enabled("Compare Schools", "Year")
    assert control_is_enabled("Global Trends", "Topic family")
    assert not control_is_enabled("Global Trends", "Country")
    assert not CONTROL_APPLICABILITY["School Finder"]


def test_dimension_options_use_selected_complete_network_view() -> None:
    dimensions = pd.DataFrame(
        [
            {
                "year": 2025,
                "corpus_view": "broad",
                "hierarchy_view": "organization",
                "country_name": "France",
            },
            {
                "year": 2025,
                "corpus_view": "broad",
                "hierarchy_view": "organization",
                "country_name": "Japan",
            },
            {
                "year": 2024,
                "corpus_view": "broad",
                "hierarchy_view": "organization",
                "country_name": "Brazil",
            },
        ]
    )
    assert dimension_options(
        dimensions,
        "country_name",
        year=2025,
        corpus="broad",
        hierarchy="organization",
    ) == ["France", "Japan"]

    long_dimensions = pd.DataFrame(
        [
            {
                "year": 2025,
                "corpus_view": "broad",
                "hierarchy_view": "organization",
                "dimension": "country",
                "value": "France",
            },
            {
                "year": 2025,
                "corpus_view": "broad",
                "hierarchy_view": "organization",
                "dimension": "institution_type",
                "value": "education",
            },
        ]
    )
    assert dimension_options(
        long_dimensions,
        "institution_category",
        year=2025,
        corpus="broad",
        hierarchy="organization",
    ) == ["education"]


def test_geographic_filters_use_one_final_node_set_and_return_partners() -> None:
    nodes = pd.DataFrame(
        [
            {
                "institution_id": "A",
                "country_name": "France",
                "subregion": "Western Europe",
                "institution_category": "education",
            },
            {
                "institution_id": "B",
                "country_name": "Japan",
                "subregion": "Eastern Asia",
                "institution_category": "education",
            },
            {
                "institution_id": "C",
                "country_name": "France",
                "subregion": "Western Europe",
                "institution_category": "healthcare",
            },
            {
                "institution_id": "D",
                "country_name": "Japan",
                "subregion": "Eastern Asia",
                "institution_category": "healthcare",
            },
        ]
    )
    edges = pd.DataFrame(
        [
            {
                "source_id": "B",
                "target_id": "C",
                "macro_region_pair": "Asia — Europe",
                "topic_families": ["GIS"],
                "large_consortium_work_count": 0,
            },
            {
                "source_id": "A",
                "target_id": "D",
                "macro_region_pair": "Asia — Europe",
                "topic_families": ["GIS"],
                "large_consortium_work_count": 0,
            },
        ]
    )

    selected, partners, visible_edges = filter_geographic_view(
        nodes,
        edges,
        country="France",
        institution_type="education",
    )

    assert selected["institution_id"].tolist() == ["A"]
    assert list(visible_edges[["source_id", "target_id"]].itertuples(index=False, name=None)) == [
        ("A", "D")
    ]
    assert partners["institution_id"].tolist() == ["D"]


def test_partner_shares_use_collaboration_endpoints_and_sum_to_one() -> None:
    matrix = pd.DataFrame(
        [
            {
                "year": 2025,
                "geographic_level": "macro_region",
                "source_geography": "Asia",
                "target_geography": "Asia",
                "fractional_count": 4.0,
            },
            {
                "year": 2025,
                "geographic_level": "macro_region",
                "source_geography": "Asia",
                "target_geography": "Europe",
                "fractional_count": 2.0,
            },
            {
                "year": 2025,
                "geographic_level": "macro_region",
                "source_geography": "Europe",
                "target_geography": "Europe",
                "fractional_count": 1.0,
            },
        ]
    )

    result = partner_share_view(
        matrix,
        weight_column="fractional_count",
        geographic_level="macro_region",
    )

    shares = {
        (row.source_geography, row.target_geography): row.partner_share
        for row in result.itertuples()
    }
    assert shares == {
        ("Asia", "Asia"): 0.8,
        ("Asia", "Europe"): 0.2,
        ("Europe", "Asia"): 0.5,
        ("Europe", "Europe"): 0.5,
    }
    assert result.groupby(["year", "source_geography"])["partner_share"].sum().tolist() == [
        1.0,
        1.0,
    ]


def test_local_profile_retains_geographies_with_no_local_collaboration() -> None:
    matrix = pd.DataFrame(
        [
            {
                "geographic_level": "country",
                "source_geography": "CN",
                "target_geography": "CN",
                "full_count": 3.0,
            },
            {
                "geographic_level": "country",
                "source_geography": "CN",
                "target_geography": "FR",
                "full_count": 2.0,
            },
        ]
    )

    result = local_collaboration_profile(
        matrix,
        weight_column="full_count",
        geographic_level="country",
    ).set_index("geography")

    assert result.loc["CN", "local_collaboration_share"] == 0.75
    assert result.loc["CN", "local_collaboration_weight"] == 3.0
    assert result.loc["CN", "total_endpoint_weight"] == 8.0
    assert result.loc["FR", "local_collaboration_share"] == 0.0
    assert result.loc["FR", "total_endpoint_weight"] == 2.0
