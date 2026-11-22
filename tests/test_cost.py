from datetime import time

import pytest

from warmly.model.cost import (build_cost_card, cost_from_energy, cost_to_hold,
                               electric_blanket_cost, fan_heater_cost, format_pence)
from warmly.model.homes import build_home, room_params_from_epc, whole_house_params
from helpers import at, constant_forecast


def test_living_room_range():
    """Living room gas heating at 3°C outside costs 5-15p an hour, for the
    demo homes' ratings."""
    forecast = constant_forecast(3.0, at(22, 0))
    for property_type, sap in [("mid_terrace", 45), ("semi", 60), ("flat", 72)]:
        living = room_params_from_epc(property_type, sap)["living"]
        pence = cost_to_hold(living, 18.0, forecast, at(22, 18), at(22, 19))
        assert 5 <= pence <= 15, property_type


def test_whole_house():
    """A 90 m² band E home at 3°C outside costs 20-35p an hour."""
    forecast = constant_forecast(3.0, at(22, 0))
    house = whole_house_params("mid_terrace", band="E", floor_area_m2=90)
    assert 190 <= house.heat_loss_w_per_c <= 205   # "about 200 W/°C"
    assert 20 <= cost_to_hold(house, 18.0, forecast, at(22, 18), at(22, 19)) <= 35


def test_fan_and_blanket():
    assert fan_heater_cost(3) == pytest.approx(158, abs=0.5)        # 2 kW for 3 h
    assert electric_blanket_cost(1) == pytest.approx(2.6, abs=0.05)  # 100 W


def test_gas_formula():
    # 850 Wh delivered = 1 kWh of gas at 85% efficiency
    assert cost_from_energy(850, "gas") == pytest.approx(7.97)
    assert cost_from_energy(1000, "electric") == pytest.approx(26.32)


def test_format_pence():
    assert format_pence(27.6) == "about 30p"
    assert format_pence(24.0) == "about 25p"
    assert format_pence(158) == "about £1.60"
    assert format_pence(98) == "about £1.00"
    assert format_pence(2.6) == "about 3p"
    assert format_pence(8.0) == "about 8p"
    assert format_pence(9.9) == "about 10p"
    assert format_pence(0.0) == "nothing"


def test_margaret_cost_card():
    """Margaret: living room 17°C -> 18°C at 20:00, held until 22:30, -1°C outside.
    The design doc's hand check was roughly 28p."""
    margaret = build_home("margaret", "Margaret", 81, "Cowley", "mid_terrace", sap_score=45)
    forecast = constant_forecast(-1.0, at(22, 16))
    card = build_cost_card(margaret, "living", at(22, 20), 17.0, forecast, time(22, 30))
    assert 20 <= card.heat_room_tonight_p <= 35
    assert "living room until bedtime" in card.summary_text
    # Every option covers the same 2.5 hours
    assert card.fan_heater_p == pytest.approx(fan_heater_cost(2.5))
    assert card.blanket_tonight_p == pytest.approx(electric_blanket_cost(2.5))
    assert card.whole_house_per_hour_p > card.heat_room_tonight_p / 2.5

    # Her switching the heating off doesn't change "what it would cost"...
    margaret.events = [{"type": "heating_override_off", "from": at(22, 17), "to": at(23, 10)}]
    card_override = build_cost_card(margaret, "living", at(22, 20), 17.0, forecast, time(22, 30))
    assert card_override.heat_room_tonight_p == card.heat_room_tonight_p
    # ...but a broken boiler means it can't be heated with gas at all.
    margaret.events = [{"type": "boiler_failure", "at": at(22, 19)}]
    card_broken = build_cost_card(margaret, "living", at(22, 20), 17.0, forecast, time(22, 30))
    assert card_broken.heat_room_tonight_p == 0
