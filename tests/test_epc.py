"""Design doc §5.1: energy rating + property type -> tau and H."""
import pytest

from warmly.model.homes import (PROPERTY_TYPES, clamp_tau, insulation_factor,
                                room_params_from_epc, sap_from_band)


@pytest.mark.parametrize("property_type, sap, living, bedroom", [
    ("mid_terrace", 45, (40, 60), (30, 45)),   # Margaret
    ("semi", 60, (50, 50), (45, 40)),          # Frank
    ("flat", 72, (60, 40), (60, 30)),          # Joan
    ("mid_terrace", 10, (29, 83), (22, 62)),
    ("semi", 86, (76, 33), (68, 26)),
])
def test_reproduces_demo_homes(property_type, sap, living, bedroom):
    rooms = room_params_from_epc(property_type, sap)
    for room, (tau, h) in [("living", living), ("bedroom", bedroom)]:
        assert rooms[room].tau_h == pytest.approx(tau, abs=1)
        assert rooms[room].heat_loss_w_per_c == pytest.approx(h, abs=1)


def test_better_rating_cools_slower():
    saps = [10, 20, 30, 45, 55, 61.5, 74.5, 86, 96]
    for property_type in PROPERTY_TYPES:
        for room in ["living", "bedroom"]:
            taus = [room_params_from_epc(property_type, sap)[room].tau_h for sap in saps]
            assert all(a < b for a, b in zip(taus, taus[1:])), (property_type, room)


def test_fallbacks():
    assert insulation_factor(45) == pytest.approx(1.0)
    # Band only -> band midpoint
    assert sap_from_band("e") == 46.5
    assert room_params_from_epc("semi", band="E") == room_params_from_epc("semi", 46.5)
    # sap wins over band
    assert room_params_from_epc("semi", 60, band="G") == room_params_from_epc("semi", 60)
    # Neither -> SAP 55
    assert room_params_from_epc("flat") == room_params_from_epc("flat", 55)
    # Overrides applied last
    rooms = room_params_from_epc("flat", 72, overrides={"bedroom": {"tau_h": 25,
                                                                    "heat_loss_w_per_c": 50}})
    assert (rooms["bedroom"].tau_h, rooms["bedroom"].heat_loss_w_per_c) == (25, 50)
    assert rooms["bedroom"].warmup_rate_c_per_h == 2.5       # the rest kept
    # tau always inside 15-120 h
    assert clamp_tau(200) == 120 and clamp_tau(5) == 15
    for property_type in PROPERTY_TYPES:
        for sap in [1, 50, 100, 150, -20]:                    # out-of-range SAP is clamped too
            for params in room_params_from_epc(property_type, sap).values():
                assert 15 <= params.tau_h <= 120


# --------------------------------------------------------------------------- #
# EPC API lookup (Tier 3), against saved responses: no network
# --------------------------------------------------------------------------- #
import json
from datetime import date
from pathlib import Path

from warmly.model import epc_lookup
from warmly.model.homes import PROPERTY_TYPES as TYPES

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class FakeResponse:
    def __init__(self, status, data=None):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_lookup_from_fixture():
    fields = epc_lookup.epc_to_profile_fields(_fixture("epc_sample.json"))
    assert isinstance(fields["sap_score"], int) and fields["sap_score"] == 63
    assert fields["epc_band"] in "ABCDEFG" and fields["epc_band"] == "D"
    assert fields["property_type"] in TYPES and fields["property_type"] == "bungalow"
    assert fields["floor_area_m2"] == 109.0
    assert fields["lodged"] == date(2010, 10, 13)
    assert set(fields) == {"sap_score", "epc_band", "property_type", "floor_area_m2", "lodged"}
    # A 2010 certificate is over 10 years old: ask about insulation since
    assert epc_lookup.certificate_is_old(fields["lodged"], date(2026, 11, 22))
    assert not epc_lookup.certificate_is_old(date(2020, 1, 1), date(2026, 11, 22))
    # ...and the rating feeds straight into the room presets
    from warmly.model.homes import room_params_from_epc
    assert room_params_from_epc(fields["property_type"], fields["sap_score"])["living"].tau_h > 15


def test_find_epc_matches_one_home(monkeypatch):
    monkeypatch.setenv("EPC_API_KEY", "test-key")
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params))
        assert headers["Authorization"] == "Bearer test-key"
        if url.endswith("/api/domestic/search"):
            return FakeResponse(200, _fixture("epc_search_sample.json"))
        return FakeResponse(200, _fixture("epc_sample.json"))

    monkeypatch.setattr(epc_lookup.requests, "get", fake_get)
    cert = epc_lookup.find_epc("AB1 2CD", "1 Example Road")
    assert cert["energy_rating_current"] == 63
    assert calls[-1][1] == {"certificate_number": "0000-0000-0000-0000-0001"}   # the newest of two
    assert epc_lookup.find_epc("AB1 2CD", "Example Road") is None        # several homes: never guess
    assert epc_lookup.find_epc("AB1 2CD", "5 Example Road") is None      # no match
    assert not epc_lookup.matches({"addressLine1": "11, Example Road"}, "1 Example Road")


def test_lookup_never_raises(monkeypatch, tmp_path):
    # No key at all
    monkeypatch.delenv("EPC_API_KEY", raising=False)
    monkeypatch.setattr(epc_lookup, "ENV_FILE", tmp_path / "missing.env")
    assert epc_lookup.find_epc("AB1 2CD", "1 Example Road") is None
    # Network error (conftest blocks the network)
    monkeypatch.setenv("EPC_API_KEY", "test-key")
    assert epc_lookup.find_epc("AB1 2CD", "1 Example Road") is None
    # 401 Unauthorized
    monkeypatch.setattr(epc_lookup.requests, "get", lambda *a, **k: FakeResponse(401))
    assert epc_lookup.find_epc("AB1 2CD", "1 Example Road") is None
    assert epc_lookup.search_epc("AB1 2CD") == []


def test_key_from_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("EPC_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text('OTHER=1\nEPC_API_KEY="abc123"\n', encoding="utf-8")
    monkeypatch.setattr(epc_lookup, "ENV_FILE", env)
    assert epc_lookup.api_key() == "abc123"
