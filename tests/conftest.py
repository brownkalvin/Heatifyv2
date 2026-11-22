"""Shared test setup: tests must never use the network or the real cache."""
import pytest
import requests

from warmly.model import forecast


@pytest.fixture(autouse=True)
def no_network(monkeypatch, tmp_path):
    def refuse(*args, **kwargs):
        raise requests.ConnectionError("tests must not use the network")

    monkeypatch.setattr(requests, "get", refuse)
    monkeypatch.setattr(forecast, "CACHE_PATH", tmp_path / "forecast_cache.json")
    monkeypatch.setattr(forecast, "COLD_NIGHT_PATH", tmp_path / "cold_night.json")
