"""Learn how fast a real home cools, from its thermometer readings.

With the heating off, the gap to outside shrinks exponentially:

    T_in - T_out = gap_0 * exp(-t / tau)
    ln(T_in - T_out) = ln(gap_0) - t / tau

So a straight-line fit of ln(T_in - T_out) against time has slope -1/tau.

Limitation: this treats T_out as steady. If it changes a lot during the
stretch, the fit is rougher (a falling T_out makes tau look larger).
"""
import math
import statistics
from dataclasses import replace
from datetime import date, datetime, timedelta

from .forecast import temp_at
from .homes import LONDON, MIN_FIT_HOURS, MIN_FIT_NIGHTS, MIN_FIT_R2, NIGHT_WINDOW, TAU_RANGE_H
from .infer_heating import label_night
from .types import ForecastPoint, Reading, RoomParams


def fit_tau(readings: list[Reading], forecast: list[ForecastPoint]) -> float | None:
    """Fit the cooling time constant tau (hours) from one room's readings.

    Pass readings from a stretch with the heating OFF (e.g. overnight).
    Returns None if there's under 3 hours of data, the room isn't cooling,
    or the fit is poor.
    """
    valid = sorted((r for r in readings if r.temp_c is not None), key=lambda r: r.timestamp)
    if len(valid) < 3:
        return None

    span_h = (valid[-1].timestamp - valid[0].timestamp).total_seconds() / 3600
    if span_h < MIN_FIT_HOURS:
        return None

    hours, log_gaps = [], []
    for r in valid:
        gap = r.temp_c - temp_at(forecast, r.timestamp)
        if gap <= 0.5:
            return None  # too close to outside temperature to fit reliably
        hours.append((r.timestamp - valid[0].timestamp).total_seconds() / 3600)
        log_gaps.append(math.log(gap))

    slope, _intercept = statistics.linear_regression(hours, log_gaps)
    if slope >= 0:
        return None  # not cooling: heating was probably on
    r_squared = statistics.correlation(hours, log_gaps) ** 2
    if r_squared < MIN_FIT_R2:
        return None
    return -1 / slope


# --------------------------------------------------------------------------- #
# Learning tau from several nights (design doc §4.3b)
# --------------------------------------------------------------------------- #
def _night_only(readings: list[Reading], night_of: date) -> list[Reading]:
    """The readings from 22:00 on `night_of` to 07:00 next morning."""
    start = datetime.combine(night_of, NIGHT_WINDOW[0], tzinfo=LONDON)
    end = datetime.combine(night_of + timedelta(days=1), NIGHT_WINDOW[1], tzinfo=LONDON)
    return [r for r in readings if start <= r.timestamp <= end]


def _fit_on_cooling_nights(nights: list[tuple[date, list[Reading]]],
                           forecast: list[ForecastPoint], params: RoomParams) -> float | None:
    """Label each night with `params`, fit tau on the pure_cooling ones and
    return the median. None if fewer than MIN_FIT_NIGHTS nights fit."""
    fits = []
    for night_of, readings in nights:
        if label_night(readings, forecast, params, night_of) == "pure_cooling":
            tau = fit_tau(_night_only(readings, night_of), forecast)
            if tau is not None:
                fits.append(tau)
    return statistics.median(fits) if len(fits) >= MIN_FIT_NIGHTS else None


def learn_tau(nights: list[tuple[date, list[Reading]]], forecast: list[ForecastPoint],
              params: RoomParams) -> float:
    """Learn a room's tau from several nights of readings, in one fixed pass.

    Labelling needs tau and fitting needs labelled nights, so to avoid a
    circle: (1) label with the preset tau, (2) fit on the pure_cooling
    nights, (3) re-label once with the fitted tau and fit again, then stop.
    The fit is only kept if it's 15-120 h from at least 2 nights;
    otherwise the preset tau is returned.
    """
    first = _fit_on_cooling_nights(nights, forecast, params)
    if first is None:
        return params.tau_h
    second = _fit_on_cooling_nights(nights, forecast, replace(params, tau_h=first))
    if second is None or not TAU_RANGE_H[0] <= second <= TAU_RANGE_H[1]:
        return params.tau_h
    return second


def params_with_fitted_tau(params: RoomParams, tau_fit: float) -> RoomParams:
    """Use the fitted tau but keep the room's heat capacity C = H x tau
    (it comes from the property type), so H = C / tau_fit."""
    capacity = params.heat_loss_w_per_c * params.tau_h
    return replace(params, tau_h=tau_fit, heat_loss_w_per_c=capacity / tau_fit)
