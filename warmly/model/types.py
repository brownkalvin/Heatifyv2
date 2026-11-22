"""Data types shared by every Heatify module.

The results that the prediction, inference, rules and cost functions return
live here too, so there's one place to look.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Literal, Optional

Room = Literal["living", "bedroom"]

# The only event types that exist (design doc §4.2)
EVENT_TYPES = ("boiler_failure", "heating_override_off", "sensor_offline")


def check_events(events: list[dict]) -> None:
    """Raise ValueError for an unknown event type, so typos fail loudly."""
    for event in events:
        if event.get("type") not in EVENT_TYPES:
            raise ValueError(f"unknown event type {event.get('type')!r}, "
                             f"expected one of {EVENT_TYPES}")


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
@dataclass
class Reading:
    """One thermometer reading."""
    home_id: str
    room: Room
    timestamp: datetime          # tz-aware, Europe/London
    temp_c: Optional[float]      # None = no reading (offline)
    source: str                  # "synthetic" | "switchbot" | "esp32"


@dataclass
class ForecastPoint:
    """Outdoor temperature at one moment."""
    timestamp: datetime
    temp_c: float
    snow: bool = False           # hourly snowfall > 0


@dataclass
class RoomParams:
    """How one room loses and gains heat. Built by homes.room_params_from_epc()."""
    tau_h: float                 # cooling time constant, hours
    heat_loss_w_per_c: float     # H
    warmup_rate_c_per_h: float   # how fast heating raises the room, °C/hour
    warmup_wh_per_c: float       # energy to warm the air + surfaces by 1°C

    def __post_init__(self) -> None:
        # Below about 15 h a room would cool to near freezing overnight,
        # which isn't realistic for a lived-in home.
        if self.tau_h < 15:
            raise ValueError(f"tau_h must be at least 15 hours, got {self.tau_h}")


@dataclass
class HeatingWindow:
    """Heating is on from `start` to `end` at `setpoint_c`."""
    start: time                  # e.g. time(18, 0)
    end: time                    # may wrap past midnight
    setpoint_c: float


@dataclass
class HomeProfile:
    """One home: who lives there, how each room behaves, and (for synthetic
    data only) the heating schedule."""
    home_id: str
    name: str
    age: int
    area: str                    # e.g. "Cowley"
    property_type: str           # "mid_terrace" | "semi" | "flat" | "detached" | "bungalow"
    sap_score: Optional[int]     # EPC energy efficiency rating, 1-100. None -> from epc_band, else 55
    rooms: dict[Room, RoomParams]                # built by room_params_from_epc()
    schedule: dict[Room, list[HeatingWindow]]    # SYNTHETIC DATA ONLY, never read by predictions
    events: list[dict] = field(default_factory=list)   # see EVENT_TYPES
    story: str = ""
    epc_band: Optional[str] = None               # "A".."G"; used only if sap_score is None
    floor_area_m2: Optional[float] = None        # from the EPC; scales the whole-house cost only
    room_overrides: dict[Room, dict] = field(default_factory=dict)  # e.g. an open bedroom window

    def __post_init__(self) -> None:
        check_events(self.events)


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
@dataclass
class OvernightPrediction:
    home_id: str
    room: Room
    predicted_min_c: float
    predicted_min_at: datetime
    predicted_at_7am_c: float
    hours_below_18: float
    will_breach_18: bool
    curve: list[tuple[datetime, float]]
    assumed_start: bool = False  # True = no real reading, started from an assumed 18°C
    scenario: str = "off"


@dataclass
class DaytimeStatus:
    below_18_for_h: float        # time below 18 today, counted from 07:00 at the earliest
    warming: bool                # slope over the last SLOPE_WINDOW_MIN > +0.2°C/h
    heating_on: Optional[bool]   # from infer_heating; None if unknown or not asked
    should_check_in: bool        # below 18 for >= 2 h AND not warming AND daytime


@dataclass
class NightSummary:
    night_of: date
    min_c: Optional[float]       # None if there were no readings at all
    min_at: Optional[datetime]
    cold_from: Optional[datetime]   # first time below 18
    cold_to: Optional[datetime]     # last time below 18
    hours_below_18: float
    breached_danger_floor: bool
    missing_fraction: float      # share of readings that were None

    @property
    def no_reading(self) -> bool:
        """True if too many readings were missing to trust the summary."""
        from .homes import MISSING_LIMIT   # imported here: homes.py imports this file
        return self.missing_fraction > MISSING_LIMIT


@dataclass
class NightOutlook:
    night_of: date
    days_ahead: int                   # 1 = tonight
    predicted_min_c: float            # heating-off case
    range_c: tuple[float, float]      # (low, high), forecast shifted by -/+ its error
    confidence: str                   # "likely" (days 1-3) | "possible" (days 4-7)
    snow: bool
    freezing: bool


@dataclass
class HeatingInference:
    heating_on: Optional[bool]        # None = not enough data
    residual_c_per_h: Optional[float]  # observed slope - expected free-cooling slope
    est_power_w: Optional[float]      # residual x H x tau, rough; only when heating_on


@dataclass
class CostCard:
    heat_room_tonight_p: float        # gas central heating, this room to 18°C until bedtime
    fan_heater_p: float               # same period
    blanket_per_hour_p: float
    summary_text: str                 # e.g. "Heating your living room until bedtime: about 25p tonight"
    blanket_tonight_p: float = 0.0    # same period as the other two
    whole_house_per_hour_p: Optional[float] = None


@dataclass
class WeeklySummary:
    cold_nights: int                          # nights with >= 2 h below 18°C
    nights: list[tuple[date, float, float]]   # (night_of, min_c, hours_below_18)
    text: str                                 # e.g. "Bedroom below 18°C for 2+ hours on 4 of 7 nights (lowest 11.6°C)"
