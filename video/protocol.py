"""Recording protocols: an ordered list of steps to film in one session.

A protocol turns "record a video" into a guided sequence — rest, head turn,
finger tapping right, finger tapping left — where each step becomes one
recorded clip and, later, one ``Segment``.

The central simplification: **a single paradigm is a protocol of length one**
(:func:`protocol_for_paradigm`).  Choosing "just a finger tapping on video"
therefore runs the exact same machinery as a full protocol, and neither the
recording flow nor the analysis needs a special case.

Pure data + validation; no Qt, no capture, no MediaPipe — so it stays testable
without hardware.  See SESSION_KONZEPT.md for the surrounding design.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

PROTOCOLS_DIR = Path(__file__).parent / "protocols"

HANDS = ("left", "right", "both")

# Fallbacks when a protocol (or a step) says nothing.
DEFAULT_DURATION_S = 20.0
DEFAULT_COUNTDOWN_S = 3.0


class ProtocolError(ValueError):
    """A protocol file is unusable (unknown paradigm, malformed step, ...)."""


@dataclass(frozen=True)
class Issue:
    """One validation finding.

    ``severity`` is ``"error"`` (protocol refuses to load) or ``"note"`` (it
    loads, but the UI should say something — e.g. a step that only works with
    face tracking switched on).
    """

    message: str
    severity: str = "error"
    step_id: str = ""

    @property
    def is_error(self) -> bool:
        return self.severity == "error"


@dataclass(frozen=True)
class ProtocolStep:
    """One thing to film. Becomes exactly one ``Segment``."""

    id: str
    title: str
    instruction: str = ""
    duration_s: float = DEFAULT_DURATION_S
    countdown_s: float = DEFAULT_COUNTDOWN_S
    paradigm: str = ""          # "" = documentation only, never analysed
    hand: str = "both"          # left | right | both

    @property
    def is_documentation(self) -> bool:
        """Filmed and archived, but no analysis is offered for it."""
        return not self.paradigm


@dataclass(frozen=True)
class Protocol:
    """An ordered list of steps, from a YAML file or built ad hoc."""

    id: str
    name: str
    description: str = ""
    steps: tuple[ProtocolStep, ...] = ()
    source_path: str = ""       # "" for an ad-hoc single-paradigm protocol
    notes: tuple[Issue, ...] = field(default_factory=tuple)

    @property
    def is_ad_hoc(self) -> bool:
        return not self.source_path

    @property
    def total_duration_s(self) -> float:
        """Net recording time — countdowns and review pauses not included."""
        return sum(s.duration_s for s in self.steps)

    def step(self, step_id: str) -> ProtocolStep | None:
        return next((s for s in self.steps if s.id == step_id), None)


# ── building ────────────────────────────────────────────────────────

def _step_from_dict(raw: dict, defaults: dict, index: int) -> ProtocolStep:
    """Build a step, filling gaps from the protocol's `defaults:` block."""
    if not isinstance(raw, dict):
        raise ProtocolError(f"Schritt {index + 1} ist kein Mapping: {raw!r}")

    step_id = str(raw.get("id") or f"step_{index + 1}")
    title = str(raw.get("title") or step_id)

    def pick(key, fallback):
        if raw.get(key) is not None:
            return raw[key]
        if defaults.get(key) is not None:
            return defaults[key]
        return fallback

    try:
        duration = float(pick("duration_s", DEFAULT_DURATION_S))
        countdown = float(pick("countdown_s", DEFAULT_COUNTDOWN_S))
    except (TypeError, ValueError) as e:
        raise ProtocolError(f"Schritt '{step_id}': Dauer/Countdown ungültig ({e})")

    return ProtocolStep(
        id=step_id,
        title=title,
        instruction=str(raw.get("instruction") or ""),
        duration_s=duration,
        countdown_s=countdown,
        paradigm=str(raw.get("paradigm") or ""),
        hand=str(pick("hand", "both")),
    )


def protocol_from_dict(data: dict, source_path: str = "") -> Protocol:
    """Build a protocol from parsed YAML **without** validating it."""
    if not isinstance(data, dict):
        raise ProtocolError("Protokolldatei enthält kein Mapping.")

    raw_steps = data.get("steps") or []
    if not isinstance(raw_steps, list):
        raise ProtocolError("`steps:` muss eine Liste sein.")

    defaults = data.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ProtocolError("`defaults:` muss ein Mapping sein.")

    steps = tuple(_step_from_dict(raw, defaults, i)
                  for i, raw in enumerate(raw_steps))

    pid = str(data.get("id") or Path(source_path).stem or "unbenannt")
    return Protocol(
        id=pid,
        name=str(data.get("name") or pid),
        description=str(data.get("description") or ""),
        steps=steps,
        source_path=source_path,
    )


def protocol_for_paradigm(paradigm_key: str, hand: str = "right",
                          duration_s: float | None = None) -> Protocol:
    """A protocol of length one — the "just record this paradigm" case.

    Deliberately the same type as a file-backed protocol, so the recording
    flow, the review/repeat cycle and the analysis have no second code path.
    """
    from paradigms import registry

    spec = registry.get(paradigm_key)   # raises for an unknown key
    label = (spec.label or paradigm_key).replace("\n", " ").strip()
    step = ProtocolStep(
        id=paradigm_key,
        title=label,
        instruction=spec.description or "",
        duration_s=float(duration_s) if duration_s else DEFAULT_DURATION_S,
        paradigm=paradigm_key,
        hand=hand,
    )
    protocol = Protocol(id=f"single:{paradigm_key}", name=label,
                        description="Einzelnes Paradigma", steps=(step,))
    return replace(protocol, notes=tuple(validate(protocol)))


# ── validation ──────────────────────────────────────────────────────

def _webcam_capabilities() -> tuple[set[str], set[str]]:
    """(always available, available only with face tracking) for a webcam.

    A webcam claims absolute position *dynamically*, while the eye reference is
    running — so a step needing it is not invalid, it just also needs face
    tracking.  Treating that as an error would wrongly reject the tremor
    paradigms, which do work on camera.
    """
    from capture.source import CAP_ABS_POSITION, WEBCAM, source_capabilities

    always = set(source_capabilities(WEBCAM))
    return always, {CAP_ABS_POSITION}


def validate(protocol: Protocol) -> list[Issue]:
    """Check a protocol against the paradigm registry and camera capabilities.

    Errors make it unusable; notes are worth showing but do not block.
    """
    from capture.source import CAP_LABELS
    from paradigms import registry
    from paradigms.config import get_task_requirements

    issues: list[Issue] = []

    if not protocol.steps:
        issues.append(Issue("Protokoll enthält keine Schritte."))
        return issues

    seen: set[str] = set()
    always, with_face = _webcam_capabilities()

    for step in protocol.steps:
        if step.id in seen:
            issues.append(Issue(f"Doppelte Schritt-ID '{step.id}'.", step_id=step.id))
        seen.add(step.id)

        if step.duration_s <= 0:
            issues.append(Issue(
                f"Dauer muss größer als 0 sein (ist {step.duration_s:g}).",
                step_id=step.id))
        if step.countdown_s < 0:
            issues.append(Issue("Countdown darf nicht negativ sein.", step_id=step.id))
        if step.hand not in HANDS:
            issues.append(Issue(
                f"Unbekannte Hand '{step.hand}' (erlaubt: {', '.join(HANDS)}).",
                step_id=step.id))

        if step.is_documentation:
            continue    # nothing else to check — it is never analysed

        try:
            spec = registry.get(step.paradigm)
        except Exception:
            issues.append(Issue(
                f"Unbekanntes Paradigma '{step.paradigm}'.", step_id=step.id))
            continue

        # A screen task (Hanoi, SRT, TMT, saccades) cannot be filmed as a step:
        # the patient responds to stimuli, the measurement lives in the
        # interaction. Those run live, outside any protocol.
        if spec.screen != registry.SCREEN_METRIC:
            issues.append(Issue(
                f"'{step.paradigm}' ist eine interaktive Bildschirm-Aufgabe und "
                "kann nicht als Video-Schritt aufgenommen werden.", step_id=step.id))
            continue

        if not spec.bilateral and step.hand == "both":
            issues.append(Issue(
                f"'{step.paradigm}' wird einseitig ausgewertet — "
                "'both' lässt die Seite offen.",
                severity="note", step_id=step.id))

        # Can a camera actually deliver what this paradigm needs?
        missing = get_task_requirements(step.paradigm) - always
        blocked = missing - with_face
        if blocked:
            names = ", ".join(sorted(CAP_LABELS.get(c, c) for c in blocked))
            issues.append(Issue(
                f"'{step.paradigm}' braucht {names} — von einer Kamera nicht "
                "lieferbar.", step_id=step.id))
        elif missing:
            names = ", ".join(sorted(CAP_LABELS.get(c, c) for c in missing))
            issues.append(Issue(
                f"'{step.paradigm}' braucht {names}; dafür muss das "
                "Gesichts-Tracking aktiv sein.",
                severity="note", step_id=step.id))

    return issues


# ── loading ─────────────────────────────────────────────────────────

def load_protocol_file(path: str | Path) -> Protocol:
    """Load and validate one protocol file. Raises ``ProtocolError``."""
    path = Path(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        raise ProtocolError(f"Protokoll nicht gefunden: {path}")
    except yaml.YAMLError as e:
        raise ProtocolError(f"{path.name}: YAML-Fehler — {e}")

    protocol = protocol_from_dict(data, source_path=str(path))
    issues = validate(protocol)
    errors = [i for i in issues if i.is_error]
    if errors:
        detail = "\n".join(
            f"  - {i.step_id + ': ' if i.step_id else ''}{i.message}" for i in errors)
        raise ProtocolError(f"{path.name} ist fehlerhaft:\n{detail}")

    return replace(protocol, notes=tuple(issues))


def load_protocol(protocol_id: str) -> Protocol:
    """Load a protocol by its id (filename stem under ``video/protocols/``)."""
    return load_protocol_file(PROTOCOLS_DIR / f"{protocol_id}.yaml")


def list_protocols() -> list[Protocol]:
    """All usable protocols, sorted by name.

    A broken file is skipped rather than taken down the whole list — one bad
    protocol must not make the others unreachable. It is logged so it does not
    vanish silently.
    """
    import logging
    log = logging.getLogger(__name__)

    out: list[Protocol] = []
    if not PROTOCOLS_DIR.is_dir():
        return out
    for path in sorted(PROTOCOLS_DIR.glob("*.yaml")):
        try:
            out.append(load_protocol_file(path))
        except ProtocolError as e:
            log.warning("Protokoll übersprungen: %s", e)
    return sorted(out, key=lambda p: p.name.lower())
