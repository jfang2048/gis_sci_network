import pytest

from gisnet.visualization.dashboard_data import _validate_public_metadata


def test_dashboard_metadata_rejects_secrets_and_private_paths() -> None:
    _validate_public_metadata({"source_policy": "processed only"})
    with pytest.raises(ValueError, match="forbidden"):
        _validate_public_metadata({"path": "/home/person/private"})
    with pytest.raises(ValueError, match="forbidden"):
        _validate_public_metadata({"value": "OPENALEX_API_KEY=secret"})
