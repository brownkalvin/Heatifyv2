"""Run the demo night and print what Heatify would see, predict and send.

Uses ONLY warmly.model.api, as a proof that the contract is enough.

    python scripts/run_demo_night.py                     # live forecast (or cache/fallback)
    python scripts/run_demo_night.py --offline           # hard-coded cold night
    python scripts/run_demo_night.py --cold              # last winter's coldest night
    python scripts/run_demo_night.py --replay 2025-01-08 # a real past night
    python scripts/run_demo_night.py --plot              # also save data/demo_night.png
"""
import argparse
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

# Let the script find the warmly package when run from anywhere
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from warmly.model import api  # noqa: E402

# What the pitch slides said (hand estimates). The model's figures replace them.
PITCH_FIGURES = {
    "Margaret: heat living room until bedtime": 25,
    "Margaret: fan heater for the same time": 160,
    "Eileen: keep bedroom at 18°C overnight": 40,
    "Eileen: warm bedroom 1 h before bed": 8,
    "Electric blanket, per hour": 3,
}


def at_time(demo: api.DemoNight, clock: time, next_day: bool = False) -> datetime:
    """A time on the demo night (or the morning after)."""
    day = demo.night_of + timedelta(days=1) if next_day else demo.night_of
    return demo.start.replace(year=day.year, month=day.month, day=day.day,
                              hour=clock.hour, minute=clock.minute)


def dashboard_at(demo: api.DemoNight, when: datetime) -> dict[str, api.HomeState]:
    """Every home's dashboard card at `when`, by home_id."""
    demo.clock.jump_to(when)
    return {h.home_id: h for h in api.dashboard_state(when, demo.homes, demo.source, demo.forecast)}


def room(state: api.HomeState, name: str) -> api.RoomState:
    return next(r for r in state.rooms if r.room == name)


def show(temp_c) -> str:
    return "no reading" if temp_c is None else f"{temp_c:.1f}°C"


def run_messages(demo: api.DemoNight) -> list[tuple[datetime, api.OutgoingMessage]]:
    """The night through pending_messages, as the backend would run it, with
    every question answered "2" straight away."""
    convo = {h.home_id: api.ConversationState(h.home_id) for h in demo.homes}
    sent = []
    now = demo.start
    while now <= demo.end:
        demo.clock.jump_to(now)
        for message in api.pending_messages(now, demo.homes, demo.source, demo.forecast, convo):
            api.record_sent(convo[message.home_id], message, now)
            sent.append((now, message))
            if convo[message.home_id].awaiting_reply:
                api.record_reply(convo[message.home_id], "2", now)
        now += timedelta(minutes=15)
    return sent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offline", action="store_true", help="use the hard-coded cold night")
    parser.add_argument("--cold", action="store_true", help="replay last winter's coldest night")
    parser.add_argument("--replay", metavar="YYYY-MM-DD", help="replay a real past night")
    parser.add_argument("--plot", action="store_true", help="save data/demo_night.png")
    args = parser.parse_args()

    # 1. Forecast
    mode = "offline" if args.offline else "cold" if args.cold else "replay" if args.replay else "live"
    replay = date.fromisoformat(args.replay) if args.replay else None
    forecast, source, night_of = api.load_demo_forecast(mode, replay)
    demo = api.start_demo(forecast, night_of)
    low = min(p.temp_c for p in forecast if demo.start <= p.timestamp <= demo.end)
    print(f"Forecast on screen: {source}")
    print(f"Demo night: {demo.start:%a %d %b %Y %H:%M} to {demo.end:%a %d %b %H:%M}, "
          f"outdoor low {low:.1f}°C")
    if low > 2 and mode == "live":
        print("   Mild night: try --cold to replay a real cold one.")
    print()

    # 2. Readings
    print(f"Wrote {api.export_demo_readings(demo)}\n")

    # 3. Each home, through the dashboard
    five_pm = dashboard_at(demo, at_time(demo, time(17)))
    eight_pm = dashboard_at(demo, at_time(demo, time(20)))
    ten_pm = dashboard_at(demo, at_time(demo, time(22)))
    bedtime = dashboard_at(demo, at_time(demo, time(20, 30)))
    morning = dashboard_at(demo, demo.end)
    messages = run_messages(demo)

    for home in demo.homes:
        hid = home.home_id
        print(f"== {home.name}, {home.age}, {home.area} ({home.property_type}, "
              f"SAP {home.sap_score}) — {home.story}")
        crossing = room(five_pm[hid], "living").crosses_18_at
        print("   17:00 dashboard: " + (f"living room predicted below 18°C at ~{crossing:%H:%M}"
                                         if crossing else five_pm[hid].status_text))
        print(f"   Living room: 20:00 {show(room(eight_pm[hid], 'living').temp_c)}, "
              f"22:00 {show(room(ten_pm[hid], 'living').temp_c)}")
        night = [(t, c) for t, c in room(morning[hid], "bedroom").history if c is not None]
        if night:
            coldest_at, coldest = min(night, key=lambda point: point[1])
            print(f"   Bedroom minimum: {coldest:.1f}°C at {coldest_at:%H:%M}")
        p = bedtime[hid].tonight
        kind = "ESTIMATE (no reading)" if p.assumed_start else "if the heating stays off"
        print(f"   20:30 prediction, {kind}: about {p.predicted_at_7am_c:.0f}°C by 7am"
              + ("" if p.will_breach_18 else ", stays at or above 18°C"))
        print(f"   Dashboard at 10:00: {morning[hid].status_text}")
        for when, message in messages:
            if message.home_id == hid:
                print(f"   {when:%H:%M} -> {message.to:7} {message.template_id:16} {message.text}")
        print()

    # 4. Margaret's cost card and Eileen's bedtime options
    card = eight_pm["margaret"].cost_card
    print("== Margaret's cost card (20:00, living room, until 22:30)")
    print(f"   {card.summary_text}\n")

    eileen = next(h for h in demo.homes if h.home_id == "eileen")
    demo.clock.jump_to(at_time(demo, time(20, 30)))
    options = {o.scenario: o for o in api.bedtime_options(eileen, at_time(demo, time(20, 30)),
                                                          demo.source, demo.forecast)}
    print(f"== Eileen's bedtime options (20:30, bedroom at "
          f"{show(room(bedtime['eileen'], 'bedroom').temp_c)})")
    for option in options.values():
        print(f"   {option.scenario:22} min {option.predicted_min_c:4.1f}°C, "
              f"cost {option.cost_p:5.1f}p")
    print()

    # 5. Pitch figures vs model figures
    model_figures = [card.heat_room_tonight_p, card.fan_heater_p,
                     options["hold_target_overnight"].cost_p,
                     options["warm_1h_before_bed"].cost_p, card.blanket_per_hour_p]
    print("== Pitch figure vs model figure (update the slides)")
    for (label, pitch_p), model_p in zip(PITCH_FIGURES.items(), model_figures):
        print(f"   {label:42} pitch {pitch_p:4.0f}p   model {model_p:6.1f}p")

    # 6. Optional plot
    if args.plot:
        save_plot(morning, forecast, demo)


def save_plot(states: dict[str, api.HomeState], forecast, demo: api.DemoNight) -> None:
    """Save data/demo_night.png if matplotlib is installed."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n--plot needs matplotlib (pip install matplotlib). Skipped.")
        return

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), sharey=True)
    outdoor = [p for p in forecast if demo.start <= p.timestamp <= demo.end]
    for ax, name in zip(axes, ["living", "bedroom"]):
        for home_id, state in states.items():
            history = room(state, name).history
            ax.plot([t for t, _ in history],
                    [c if c is not None else float("nan") for _, c in history], label=home_id)
        ax.plot([p.timestamp for p in outdoor], [p.temp_c for p in outdoor],
                color="grey", linestyle=":", label="outdoor")
        ax.axhline(18, color="black", linewidth=0.8, linestyle="--")
        ax.set_title(name)
        ax.set_ylabel("°C")
        ax.tick_params(axis="x", rotation=45)
    axes[1].legend(fontsize=8)
    fig.tight_layout()
    path = PROJECT_ROOT / "data" / "demo_night.png"
    fig.savefig(path, dpi=120)
    print(f"\nSaved {path}")


if __name__ == "__main__":
    main()
