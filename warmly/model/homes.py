"""Presets and thresholds: the ONLY place for Heatify's constants.

The numbers are rough guesses, tuned so the model's outputs land near the
pitch figures. Edit them here and nowhere else.
"""
from datetime import time
from zoneinfo import ZoneInfo

from .types import HeatingWindow, HomeProfile, Room, RoomParams

LONDON = ZoneInfo("Europe/London")

# --------------------------------------------------------------------------- #
# Thresholds
# --------------------------------------------------------------------------- #
TARGET_C = 18.0                 # NHS: at least 18°C in living room and bedroom
DANGER_FLOOR_C = 10.0           # PLACEHOLDER for the demo only. To be agreed with advisers. Not health advice.
DAYTIME_WINDOW = (time(7), time(22))
BEDTIME_CHECK = time(20, 30)
BEDTIME = time(22, 0)           # assumed bedtime for scenarios and the weekly outlook
NIGHT_WINDOW = (time(22), time(7))
HEADS_UP_C = 16.0               # the family's first ping: the room in use will drop below
HEADS_UP_H = 2.0                # this within this many hours if the heating stays off

# Daytime check-in and slope (design doc §4.3)
CHECK_IN_AFTER_H = 2.0          # below 18°C for at least this long...
WARMING_SLOPE_C_PER_H = 0.2     # ...and not rising faster than this
SLOPE_WINDOW_MIN = 60           # slope is measured over the last 60 minutes...
SLOPE_MIN_READINGS = 4          # ...from at least this many readings
MISSING_LIMIT = 0.5             # more than half the readings missing = "no reading"

# Heating inference (design doc §4.3b)
INFER_WINDOW_MIN = 90           # heating inference looks at the last 90 minutes...
INFER_MIN_READINGS = 6          # ...from at least this many readings
SLOPE_SE_FLOOR = 0.05           # never trust a slope's standard error below this (°C/h)
RISE_WINDOW_MIN = 30            # a room that warmed by RISE_MIN_C in the last 30 minutes,
RISE_MIN_C = 0.5                # on a night colder than the room, has its heating on
USUALLY_ON_NIGHTS = 5           # "heating usually on at night" = on for 5+ of the last 7 known nights

# Weekly outlook (design doc §4.3): rough forecast error, by days ahead
FORECAST_ERROR_C = {1: 1.0, 2: 1.0, 3: 1.0, 4: 2.0, 5: 2.0, 6: 3.0, 7: 3.0}
LIKELY_DAYS = 3                 # days 1-3 are "likely", days 4-7 "possible"

# Trigger-engine rules (design doc §4.7, §4.8)
DECLINES_BEFORE_BACKOFF = 3     # after this many recent "no thanks", only the coldest nights
MORNING_CHECK_AFTER_H = 2.0     # morning check-in if the bedroom was below 18°C this long
NO_READING_MAX_AGE_H = 2.0      # a reading younger than this still counts
SENSOR_OFFLINE_ALERT_MIN = 120  # family gets the no_reading message after this long

# Dashboard colours (design doc §6): green >= TARGET_C, amber >= this, red below
AMBER_FROM_C = 13.0

# Family weekly summary: sent on this weekday (Monday = 0, Sunday = 6) from 17:00
WEEKLY_SUMMARY_WEEKDAY = 6

# Escalation and the cost card (design doc §4.11, §7)
REPLY_TIMEOUT_MIN = 30          # simulated minutes before the next escalation step
COST_CARD_UNTIL = time(22, 30)  # the living-room cost card covers now -> 22:30

# Real sensor data hygiene (design doc §4.12)
STALE_AFTER_MIN = 45            # newest reading older than this = "no reading"
SPIKE_C = 2.0                   # drop a reading this far from its neighbours' median
READING_RANGE_C = (-10.0, 40.0) # anything outside this is a sensor fault

# --------------------------------------------------------------------------- #
# Prices (Ofgem cap Oct–Dec 2026, Direct Debit)
# --------------------------------------------------------------------------- #
GAS_P_PER_KWH = 7.97
ELEC_P_PER_KWH = 26.32
BOILER_EFFICIENCY = 0.85
FAN_HEATER_W = 2000
ELECTRIC_BLANKET_W = 100

# --------------------------------------------------------------------------- #
# Synthetic data realism (design doc §6)
# --------------------------------------------------------------------------- #
NOISE_SIGMA_C = 0.08            # sensor noise
READING_STEP_MIN = 15           # one reading every 15 minutes
DEFAULT_START_C = 18.5          # start temperature at 16:00 when heating is off

# --------------------------------------------------------------------------- #
# Calibration (design doc §4.3b, §4.5)
# --------------------------------------------------------------------------- #
MIN_FIT_HOURS = 3.0             # need at least 3 hours of cooling
MIN_FIT_R2 = 0.8                # reject fits worse than this
MIN_FIT_NIGHTS = 2              # need at least 2 heating-off nights to replace the preset
TAU_RANGE_H = (15.0, 120.0)     # tau is always kept inside this range

# --------------------------------------------------------------------------- #
# Energy rating -> how fast each room cools (design doc §5.1)
#
# Property type sets each room's heat capacity C (Wh/°C).
# The EPC rating (SAP score) sets the heat-loss coefficient H (W/°C).
# Then tau = C / H.
# --------------------------------------------------------------------------- #
DEFAULT_SAP = 55                # "don't know": a typical band D home
SAP_BAND_MIDPOINT = {"A": 96, "B": 86, "C": 74.5, "D": 61.5, "E": 46.5, "F": 29.5, "G": 10.5}

# property_type: {room: (H_ref at SAP 45 in W/°C, C in Wh/°C)}, plus warm-up (°C/h, Wh/°C)
PROPERTY_TYPES: dict[str, dict] = {
    "mid_terrace": {"living": (60.0, 2400), "bedroom": (45.0, 1350), "warmup": (1.5, 300)},
    "semi":        {"living": (59.8, 2500), "bedroom": (47.8, 1800), "warmup": (2.0, 300)},
    "flat":        {"living": (56.7, 2400), "bedroom": (42.5, 1800), "warmup": (2.5, 200)},
    "detached":    {"living": (75.0, 2700), "bedroom": (55.0, 1800), "warmup": (2.0, 350)},  # guess
    "bungalow":    {"living": (70.0, 2500), "bedroom": (50.0, 1700), "warmup": (2.0, 300)},  # guess
}

# Whole house (design doc §5.2): H_house = 2.2 W/m²°C x floor area x insulation factor
WHOLE_HOUSE_W_PER_M2_C = 2.2    # rough guess
DEFAULT_FLOOR_AREA_M2 = 90.0
EPC_MAX_AGE_YEARS = 10           # older certificates: ask if the home's been insulated since


def insulation_factor(sap: float) -> float:
    """1.0 at SAP 45 (mid band E). Lower = better insulated."""
    return 0.4 + 0.6 * (100 - sap) / 55


def sap_from_band(band: str) -> float:
    """The middle SAP score of an EPC band letter, e.g. "E" -> 46.5."""
    return SAP_BAND_MIDPOINT[band.strip().upper()]


def resolve_sap(sap: float | None = None, band: str | None = None) -> float:
    """The SAP score to use: `sap` wins over `band`; neither -> DEFAULT_SAP.
    Kept inside 1-100."""
    if sap is None:
        sap = sap_from_band(band) if band else DEFAULT_SAP
    return min(100.0, max(1.0, float(sap)))


def clamp_tau(tau_h: float) -> float:
    """Keep tau inside TAU_RANGE_H (15-120 h)."""
    low, high = TAU_RANGE_H
    return min(high, max(low, tau_h))


def room_params_from_epc(property_type: str, sap: float | None = None, band: str | None = None,
                         overrides: dict | None = None) -> dict[Room, RoomParams]:
    """Each room's RoomParams from the property type and energy rating.

    H = H_ref[type][room] x insulation_factor(sap);  tau = C[type][room] / H.
    sap wins over band; neither given -> SAP 55. tau is clamped to 15-120 h.
    `overrides` (e.g. {"bedroom": {"tau_h": 25, "heat_loss_w_per_c": 50}}
    for an open window) are applied last.
    """
    factor = insulation_factor(resolve_sap(sap, band))
    preset = PROPERTY_TYPES[property_type]
    rate, warmup_wh = preset["warmup"]
    rooms: dict[Room, RoomParams] = {}
    for room in ("living", "bedroom"):
        h_ref, capacity = preset[room]
        h = h_ref * factor
        values = {"tau_h": clamp_tau(capacity / h), "heat_loss_w_per_c": h,
                  "warmup_rate_c_per_h": rate, "warmup_wh_per_c": warmup_wh}
        values.update((overrides or {}).get(room, {}))
        rooms[room] = RoomParams(**values)
    return rooms


def whole_house_params(property_type: str, sap: float | None = None, band: str | None = None,
                       floor_area_m2: float | None = None) -> RoomParams:
    """The whole house as one "room", for the cost card's whole-house figure.
    Only H matters for cost_to_hold(); the rest is copied from the living room."""
    living = room_params_from_epc(property_type, sap, band)["living"]
    area = floor_area_m2 or DEFAULT_FLOOR_AREA_M2
    h = WHOLE_HOUSE_W_PER_M2_C * area * insulation_factor(resolve_sap(sap, band))
    return RoomParams(living.tau_h, h, living.warmup_rate_c_per_h, living.warmup_wh_per_c)


def build_home(home_id: str, name: str, age: int, area: str, property_type: str,
               sap_score: int | None = None, epc_band: str | None = None,
               schedule: dict[Room, list[HeatingWindow]] | None = None,
               events: list[dict] | None = None, story: str = "",
               floor_area_m2: float | None = None,
               room_overrides: dict[Room, dict] | None = None) -> HomeProfile:
    """A HomeProfile with its rooms worked out from the energy rating."""
    overrides = room_overrides or {}
    return HomeProfile(
        home_id=home_id, name=name, age=age, area=area, property_type=property_type,
        sap_score=sap_score,
        rooms=room_params_from_epc(property_type, sap_score, epc_band, overrides),
        schedule=schedule or {"living": [], "bedroom": []},
        events=events or [], story=story, epc_band=epc_band,
        floor_area_m2=floor_area_m2, room_overrides=overrides,
    )
