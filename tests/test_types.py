import pytest

from warmly.model.homes import build_home
from warmly.model.room_model import simulate
from warmly.model.types import RoomParams
from helpers import at, constant_forecast


def test_unknown_event():
    with pytest.raises(ValueError):
        build_home("x", "Sam", 80, "Iffley", "flat", events=[{"type": "boiler_fail", "at": at(22, 1)}])
    with pytest.raises(ValueError):
        simulate(18.0, at(22, 16), at(22, 18), RoomParams(40, 60, 1.5, 300), [],
                 constant_forecast(3.0, at(22, 16)), events=[{"type": "typo"}])
    # The three real types are fine; sensor_offline doesn't touch the physics
    events = [{"type": "boiler_failure", "at": at(23, 1)},
              {"type": "heating_override_off", "from": at(22, 17), "to": at(22, 18)},
              {"type": "sensor_offline", "room": "living", "from": at(22, 17)}]
    curve = simulate(18.0, at(22, 16), at(22, 18), RoomParams(40, 60, 1.5, 300), [],
                     constant_forecast(3.0, at(22, 16)), events=events)
    assert len(curve) == 9
