"""Fixed message templates (design doc §4.10).

rules.py decides WHEN a message is due; this file decides WHAT it says.
The wording is fixed and only filled with the model's numbers, because the
safety lines ("call 999", "if your heating stays off") must never vary.

Wording rules (tests/test_messages.py checks them):
- person-facing messages are at most 300 characters (2 SMS);
- nothing person-facing says "danger", "risk" or "warning";
- only family_urgent mentions 999;
- every number goes through display_temp() or display_pence().
"""
from .cost import format_pence

TEMPLATES: dict[str, str] = {
    "daytime_checkin": (
        "Hi {name}, it's Warmly. Your living room is {temp}°C and it's going down to "
        "{out_low}°C tonight. Is your heating on? Reply 1 for yes, 2 for no."),
    "daytime_checkin_predicted": (
        "Hi {name}, it's Warmly. It's going down to {out_low}°C tonight, so your living "
        "room will drop below 18°C by about {cross_time} if the heating stays off. "
        "Is your heating on? Reply 1 for yes, 2 for no."),
    "checkin_reminder": (
        "Hi {name}, it's Warmly again. Just checking you're OK. Reply 1 if you're fine, "
        "or 2 if you'd like a call."),
    "cost_followup": (
        "That's your choice, {name}. Just so you know: heating only your living room until "
        "bedtime would cost about {room_cost} tonight. A plug-in fan heater would cost about "
        "{fan_cost}. An electric blanket is about {blanket} an hour. Would you like help "
        "checking for money off your bills? Reply HELP."),
    "bedtime": (
        "Hi {name}, it's Warmly. It's going down to {out_low}°C tonight. If your heating "
        "stays off, your bedroom will probably drop to about {pred_min}°C by morning. "
        "Keeping it at 18°C overnight would cost about {hold_cost}. Or warm it for an hour "
        "before bed (about {warm_cost}) and use an electric blanket."),
    "bedtime_estimate": (
        "Hi {name}, it's Warmly. We haven't had a reading from your bedroom, so this is "
        "an estimate: it's going down to {out_low}°C tonight, and if your heating stays "
        "off your bedroom could drop to about {pred_min}°C by morning."),
    "bedtime_declined": (
        "That's up to you, {name}. There may be help with your bills; reply HELP and I'll "
        "look in the morning. Reply OK and I won't message again tonight."),
    "morning": (
        "Morning {name}. Your bedroom was about {night_min}°C overnight. How are you "
        "feeling? Reply 1 if you're OK."),
    "family_evening": (
        "{name}'s living room is {temp}°C. {reason} Nothing urgent, but a call tonight "
        "might help. Heating one room costs about {room_cost} tonight."),
    "family_night": (
        "{name}'s bedroom is {temp}°C tonight, which is very cold. You asked us to tell you "
        "at night. Please call them or ask a neighbour to check on them."),
    "family_urgent": (
        "{name}'s bedroom was very cold overnight and they haven't answered this morning. "
        "Please call them or ask a neighbour to check on them. If you think they're in "
        "danger, call 999."),
    "family_weekly": "{summary}. They've answered {answered} of {asked} check-ins.",
    "family_weekly_quiet": "{summary}. Warmly didn't need to check in with them this week.",
    "no_reading": (
        "We haven't had a reading from {name}'s {thermometers} since {last_seen}. "
        "Could you check it's switched on and near the room's centre?"),
    "sensor_placement": (
        "Put each thermometer at about head height, away from radiators, windows, outside "
        "walls and direct sun."),
}

# Who each template goes to: "person", "contact" (family) or "signup" (a screen)
AUDIENCE: dict[str, str] = {
    "daytime_checkin": "person", "daytime_checkin_predicted": "person",
    "checkin_reminder": "person", "cost_followup": "person", "bedtime": "person",
    "bedtime_estimate": "person", "bedtime_declined": "person", "morning": "person",
    "family_evening": "contact", "family_night": "contact", "family_urgent": "contact",
    "family_weekly": "contact", "family_weekly_quiet": "contact", "no_reading": "contact",
    "sensor_placement": "signup",
}

# Templates that ask the person a question, so no reply starts the escalation
EXPECTS_REPLY = {"daytime_checkin", "daytime_checkin_predicted", "checkin_reminder", "morning"}

MAX_PERSON_CHARS = 300   # 2 SMS


def render(template_id: str, **fields) -> str:
    """Fill in a template. Raises KeyError if a field is missing, so a bug
    can never send a message containing "{temp}"."""
    return TEMPLATES[template_id].format(**fields)


def display_temp(temp_c: float) -> str:
    """Round a temperature for a message, to avoid false precision:
    nearest 0.5°C below 15°C, nearest 1°C otherwise. E.g. 11.74 -> "11.5", 16.6 -> "17"."""
    if temp_c < 15:
        rounded = round(temp_c * 2) / 2
    else:
        rounded = round(temp_c)
    return f"{rounded:g}"


def display_pence(pence: float) -> str:
    """Pence for a message, rounded like the cost card but without "about"
    (the templates say "about" themselves). E.g. 22.2 -> "20p", 131.6 -> "£1.30"."""
    return format_pence(pence).removeprefix("about ")
