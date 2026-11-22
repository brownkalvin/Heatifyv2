import pytest

from warmly.model.messages import (AUDIENCE, MAX_PERSON_CHARS, TEMPLATES, display_pence,
                                   display_temp, render)

# Realistic worst-case fields: long-ish names and the widest numbers
SAMPLE = {
    "name": "Margaret", "temp": "16.5", "out_low": "-10.5", "cross_time": "18:45",
    "room_cost": "£1.30", "fan_cost": "£12.50", "blanket": "3p", "pred_min": "11.5",
    "hold_cost": "£1.10", "warm_cost": "10p", "night_min": "11.5",
    "reason": "They haven't answered our check-in.",
    "summary": "Bedroom below 18°C for 2+ hours on 4 of 7 nights (lowest 11.6°C)",
    "answered": 5, "asked": 7, "thermometers": "living room and bedroom thermometers",
    "last_seen": "18:45", "room": "bedroom",
}


def test_all_render():
    for template_id in TEMPLATES:
        text = render(template_id, **SAMPLE)
        assert "{" not in text and "}" not in text, template_id
    with pytest.raises(KeyError):
        render("daytime_checkin", name="Margaret")            # temp and out_low missing
    assert set(AUDIENCE) == set(TEMPLATES)


def test_wording_rules():
    for template_id, audience in AUDIENCE.items():
        text = render(template_id, **SAMPLE)
        if audience == "person":
            assert len(text) <= MAX_PERSON_CHARS, (template_id, len(text))
            for word in ("danger", "risk", "warning"):
                assert word not in text.lower(), (template_id, word)
        assert ("999" in text) == (template_id == "family_urgent"), template_id


def test_display_helpers():
    assert display_temp(11.74) == "11.5"
    assert display_temp(11.76) == "12"
    assert display_temp(14.8) == "15"
    assert display_temp(16.6) == "17"
    assert display_temp(-1.2) == "-1"
    assert display_pence(22.2) == "20p"
    assert display_pence(131.6) == "£1.30"
    assert display_pence(2.6) == "3p"
