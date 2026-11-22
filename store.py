"""Signed-up homes and their thermometers, saved as JSON in data/.

This is backend state (the model in warmly/ stores nothing). The files hold
personal data (names, ages, addresses' energy ratings), so data/homes.json
and data/sensors.json are gitignored.
"""
import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional

from warmly.model.homes import build_home
from warmly.model.types import HomeProfile
from warmly.sources import load_sensors

DATA_DIR = Path(__file__).resolve().parent / "data"


@dataclass
class HomeRecord:
    """One signed-up home: what we keep, and nothing from the certificate
    beyond the fields the model needs."""
    home_id: str
    name: str
    age: int
    area: str
    property_type: str
    sap_score: Optional[int] = None
    epc_band: Optional[str] = None
    floor_area_m2: Optional[float] = None
    certificate_lodged: Optional[str] = None      # ISO date, if it came from the EPC register
    contact_name: str = ""
    relationship_word: str = ""                   # e.g. "Mum"
    night_alerts_opted_in: bool = False


# Contact settings kept on the record. Phone numbers live in data/phones.json
# (server.PHONES); nothing sends texts at the moment.
SETTINGS_FIELDS = ("contact_name", "relationship_word", "night_alerts_opted_in")


def load_records(path: Path) -> list[HomeRecord]:
    """The saved homes, or [] if there's no file yet."""
    if not path.exists():
        return []
    known = {f.name for f in fields(HomeRecord)}
    return [HomeRecord(**{k: v for k, v in row.items() if k in known})
            for row in json.loads(path.read_text(encoding="utf-8"))]


def save_records(path: Path, records: list[HomeRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(r) for r in records], indent=2), encoding="utf-8")


def record_to_home(record: HomeRecord) -> HomeProfile:
    """A HomeProfile from a saved record, rooms worked out from the rating."""
    return build_home(record.home_id, record.name, record.age, record.area,
                      record.property_type, sap_score=record.sap_score,
                      epc_band=record.epc_band, floor_area_m2=record.floor_area_m2)


def load_sensor_list(path: Path) -> list[dict]:
    """The saved thermometer -> home mapping, or [] if there's no file yet."""
    return load_sensors(path) if path.exists() else []


def save_sensor_list(path: Path, sensors: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sensors, indent=2), encoding="utf-8")
    load_sensors(path)   # check what was written
