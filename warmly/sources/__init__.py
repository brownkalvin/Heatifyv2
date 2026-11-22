"""Where readings come from: synthetic demo homes or real sensors (SwitchBot or ESP32)."""
from .base import ReadingSource, RealClock, SimClock, load_sensors
from .stored_source import StoredSource
from .synthetic_source import SyntheticSource

__all__ = ["ReadingSource", "RealClock", "SimClock", "StoredSource", "SyntheticSource",
           "load_sensors"]
