"""The cost card: what heating a room to 18°C costs, compared with a fan
heater and an electric blanket.

Gas central heating: pence = energy_wh / 1000 / BOILER_EFFICIENCY * GAS_P_PER_KWH
Electric heaters and blankets are 100% efficient, at ELEC_P_PER_KWH.
Prices are set in homes.py.
"""
from datetime import datetime, time, timedelta

from .forecast import temp_at
from .homes import (BOILER_EFFICIENCY, ELEC_P_PER_KWH, ELECTRIC_BLANKET_W,
                    FAN_HEATER_W, GAS_P_PER_KWH, TARGET_C, whole_house_params)
from .predict import known_events, next_occurrence, run_scenario
from .room_model import simulate
from .types import CostCard, ForecastPoint, HeatingWindow, HomeProfile, Room, RoomParams

ROOM_NAMES = {"living": "living room", "bedroom": "bedroom"}


def cost_from_energy(energy_wh: float, fuel: str = "gas") -> float:
    """Pence for `energy_wh` of heat delivered to the room.
    fuel is "gas" (through the boiler) or "electric"."""
    kwh = energy_wh / 1000
    if fuel == "gas":
        return kwh / BOILER_EFFICIENCY * GAS_P_PER_KWH
    if fuel == "electric":
        return kwh * ELEC_P_PER_KWH
    raise ValueError(f"fuel must be 'gas' or 'electric', got {fuel!r}")


def cost_to_hold(params: RoomParams, t_set: float, forecast: list[ForecastPoint],
                 start: datetime, end: datetime) -> float:
    """Pence (gas) to keep a room already at `t_set` there from start to end.
    Steady state: power = H × (T_set − T_out), added up in 15-minute steps."""
    step = timedelta(minutes=15)
    energy_wh = 0.0
    when = start
    while when < end:
        dt = min(step, end - when)
        t_out = temp_at(forecast, when + dt / 2)
        power_w = max(0.0, params.heat_loss_w_per_c * (t_set - t_out))
        energy_wh += power_w * dt.total_seconds() / 3600
        when += dt
    return cost_from_energy(energy_wh, "gas")


def cost_of_scenario(home: HomeProfile, room: Room, now: datetime, t_now: float,
                     forecast: list[ForecastPoint], scenario: str,
                     until: time = time(7, 0)) -> float:
    """Pence (gas) for a scenario from predict.py, using the energy that
    simulate() reports."""
    sim = run_scenario(home, room, now, t_now, forecast, scenario, until)
    return cost_from_energy(sum(energy for _, _, energy in sim), "gas")


def fan_heater_cost(hours: float, watts: float = FAN_HEATER_W) -> float:
    """Pence to run an electric fan heater flat out."""
    return watts / 1000 * hours * ELEC_P_PER_KWH


def electric_blanket_cost(hours: float, watts: float = ELECTRIC_BLANKET_W) -> float:
    """Pence to run an electric blanket."""
    return watts / 1000 * hours * ELEC_P_PER_KWH


def format_pence(pence: float) -> str:
    """Round for display: under 10p to the nearest 1p, under £1 to the
    nearest 5p, otherwise to the nearest 10p.
    E.g. 2.6 -> "about 3p", 24.0 -> "about 25p", 158 -> "about £1.60"."""
    if pence < 0.5:
        return "nothing"
    if pence < 9.5:
        return f"about {round(pence)}p"
    if pence < 100:
        rounded = round(pence / 5) * 5
        if rounded < 100:
            return f"about {rounded}p"
    pounds = round(pence / 10) / 10
    return f"about £{pounds:.2f}"


def build_cost_card(home: HomeProfile, room: Room, now: datetime, t_now: float,
                    forecast: list[ForecastPoint], until: time,
                    until_label: str = "bedtime") -> CostCard:
    """Cost of heating this room to 18°C from now until `until` (bedtime),
    with a fan heater and an electric blanket for the SAME period, plus the
    whole house per hour. Figures are unrounded; summary_text is rounded."""
    end = next_occurrence(now, until)
    schedule = [HeatingWindow(now.time(), until, TARGET_C)]
    # "If you turned the heating on": ignore the person's own off switch,
    # but a broken boiler still stops it.
    sim = simulate(t_now, now, end, home.rooms[room], schedule, forecast,
                   events=known_events(home.events, now, include_overrides=False))
    heat_p = cost_from_energy(sum(energy for _, _, energy in sim), "gas")

    hours = (end - now).total_seconds() / 3600
    fan_p = fan_heater_cost(hours)
    blanket_p = electric_blanket_cost(hours)

    house = whole_house_params(home.property_type, home.sap_score, home.epc_band,
                               home.floor_area_m2)
    house_p = cost_to_hold(house, TARGET_C, forecast, now, now + timedelta(hours=1))

    summary = (f"Heating your {ROOM_NAMES[room]} until {until_label}: {format_pence(heat_p)} tonight. "
               f"A fan heater for the same time: {format_pence(fan_p)}. "
               f"An electric blanket for the same time: {format_pence(blanket_p)}. "
               f"Heating the whole house: {format_pence(house_p)} an hour.")
    return CostCard(heat_p, fan_p, electric_blanket_cost(1), summary, blanket_p, house_p)
