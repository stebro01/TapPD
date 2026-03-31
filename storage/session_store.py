"""Session storage: save/load results as JSON, export to CSV."""

import csv
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


DATA_DIR = Path(__file__).parent.parent / "data" / "sessions"


@dataclass
class SessionResult:
    patient_id: str
    test_type: str  # "finger_tapping", "hand_open_close", "tremor"
    hand: str  # "left" / "right"
    duration_s: float
    timestamp: str = ""  # ISO format
    features: dict[str, float] = field(default_factory=dict)
    raw_data: list[dict] | None = None  # Optional raw frames

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


def save_session(result: SessionResult, include_raw: bool = False) -> Path:
    """Save session to JSON. Returns the file path."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.fromisoformat(result.timestamp).strftime("%Y%m%d_%H%M%S")
    filename = f"{result.patient_id}_{result.test_type}_{ts}.json"
    filepath = DATA_DIR / filename

    data = asdict(result)
    if not include_raw:
        data.pop("raw_data", None)

    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, default=str)

    log.info("Session gespeichert: %s", filepath.name)
    return filepath


def load_session(path: str | Path) -> SessionResult:
    """Load a session from JSON."""
    with open(path) as f:
        data = json.load(f)
    return SessionResult(**data)


def export_csv(result: SessionResult, path: str | Path) -> Path:
    """Export session features as a flat CSV row."""
    log.info("CSV-Export: %s %s -> %s", result.test_type, result.hand, path)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Flatten: metadata + features
    row = {
        "patient_id": result.patient_id,
        "test_type": result.test_type,
        "hand": result.hand,
        "duration_s": result.duration_s,
        "timestamp": result.timestamp,
    }
    row.update(result.features)

    file_exists = path.exists()
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    return path
