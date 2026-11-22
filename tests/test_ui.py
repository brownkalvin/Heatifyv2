from datetime import date, timedelta

from warmly.model.forecast import load_fallback
from warmly.model.rules import home_colour, room_in_use, status_colour
from warmly.model.synthetic import build_demo_homes, demo_window, generate_curves
from helpers import at


def test_room_in_use():
    """Frank's home headline is green all night, although his living room
    is amber at 04:00."""
    assert room_in_use(at(22, 21, 59)) == "living"
    assert room_in_use(at(22, 22)) == "bedroom"
    assert room_in_use(at(23, 7)) == "living"

    night = date(2026, 11, 22)
    curves = generate_curves(build_demo_homes(night), load_fallback(night), *demo_window(night))
    frank = {room: {r.timestamp: r.temp_c for r in curves[("frank", room)]}
             for room in ["living", "bedroom"]}
    assert status_colour(frank["living"][at(23, 4, 30)]) == "amber"

    when = at(22, 16)
    while when < at(23, 7):
        temps = {room: frank[room][when] for room in frank}
        assert home_colour(temps, when) == "green", when
        when += timedelta(minutes=15)
