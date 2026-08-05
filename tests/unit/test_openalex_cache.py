from pathlib import Path

from gisnet.openalex.cache import RawResponseCache


def test_cache_key_is_stable_and_excludes_api_key() -> None:
    first = RawResponseCache.make_key("works", {"cursor": "*", "api_key": "one"})
    second = RawResponseCache.make_key("/works", {"api_key": "two", "cursor": "*"})
    assert first == second


def test_cache_round_trip_and_metadata_are_separate(tmp_path: Path) -> None:
    cache = RawResponseCache(tmp_path)
    entry = cache.put(
        endpoint="works",
        parameters={"cursor": "*", "api_key": "never-store"},
        data={"results": [{"id": "W1"}], "meta": {"next_cursor": "next"}},
        status_code=200,
        retrieved_at_utc="2026-08-05T00:00:00Z",
        rate_limit={"x-ratelimit-remaining": "99"},
    )
    loaded = cache.validate(entry.key, entry.metadata["checksum_sha256"])
    assert loaded.data["results"][0]["id"] == "W1"
    all_text = "".join(path.read_text(errors="ignore") for path in tmp_path.rglob("*.json"))
    assert "never-store" not in all_text
    data_path, metadata_path = cache._paths(entry.key)
    assert data_path.suffix == ".gz"
    assert metadata_path.exists()


def test_corrupt_cache_is_quarantined(tmp_path: Path) -> None:
    cache = RawResponseCache(tmp_path)
    entry = cache.put(
        endpoint="works",
        parameters={"cursor": "*"},
        data={"results": [], "meta": {"next_cursor": None}},
        status_code=200,
        retrieved_at_utc="2026-08-05T00:00:00Z",
    )
    data_path, _ = cache._paths(entry.key)
    data_path.write_bytes(b"corrupt")
    assert cache.get(entry.key) is None
    assert len(list((tmp_path / "quarantine").iterdir())) == 2
