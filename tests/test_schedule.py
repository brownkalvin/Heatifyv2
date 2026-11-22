from datetime import time

from warmly.model.room_model import is_heating_on
from warmly.model.types import HeatingWindow
from helpers import at


def test_wraps_midnight():
    schedule = [HeatingWindow(time(21), time(7), 18.5)]
    assert is_heating_on(at(23, 2), schedule) == (True, 18.5)
    assert is_heating_on(at(23, 12), schedule) == (False, None)
    assert is_heating_on(at(22, 21), schedule) == (True, 18.5)
    assert is_heating_on(at(23, 7), schedule) == (False, None)


def test_normal_window():
    schedule = [HeatingWindow(time(7), time(22, 30), 20.0)]
    assert is_heating_on(at(22, 22, 15), schedule) == (True, 20.0)
    assert is_heating_on(at(22, 22, 30), schedule) == (False, None)
