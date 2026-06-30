# Architecture

> Working title **TapPD**; the forward-looking platform name is **Motryx**
> (motor + metrics) — a multimodal movement-assessment lab (hand, and later
> face & oculomotor). The rename is Stage 2 (see roadmap).

This document is the single written description of how the system is layered, the
standard API each layer agrees on, the naming scheme, and the staged plan toward
multimodal support.

---

## 1. Layering overview

```
┌──────────────┐   frames    ┌───────────────┐  features  ┌──────────────┐
│   SOURCE     │ ──────────► │   PARADIGM    │ ─────────► │   STORAGE    │
│ (input/HW)   │  callback   │ (assessment)  │            │ (star schema)│
└──────────────┘             └───────────────┘            └──────────────┘
       ▲                            ▲
       │ SourceProfile              │ ReadinessGate (pre-test, source-agnostic)
       │ (caps, readiness, prompts) │
```

- **Source** — produces motion data. One abstraction, several implementations
  (Leap, webcam, simulation). Hot-swappable at runtime via the Tracking screen.
- **Paradigm** — a clinical/cognitive task that consumes frames and computes
  features. Declared once in the **registry**.
- **Storage** — i2b2-style star schema; a paradigm result is an `OBSERVATION_FACT`.
- **SourceProfile** — per-source capabilities, readiness policy and prompts; the
  seam where source-specific frame re-mapping plugs in.

---

## 2. The standard API (contracts)

### Source — `capture/base_capture.py:MotionSource`
```python
connect() -> None            # establish connection (may block on discovery/spawn)
disconnect() -> None         # tear down, stop background threads
is_connected() -> bool       # cheap health check
start_recording(cb) -> None  # stream; cb(HandFrame) per hand per frame, bg thread
stop_recording() -> None
sample_rate: float           # live Hz
```
- Structural view + shared callback type in `capture/contracts.py`
  (`MotionSourceProtocol`, `FrameConsumer`).
- Implementations: `LeapCaptureDevice`, `MediaPipeCaptureDevice` (webcam, via a
  Python-3.12 sidecar), `SimulationSource`. Factory: `capture.create_source(kind)`.
- **Profile**: `capture/source.py:SourceProfile` — `capabilities`, `hand_ready(frame)`
  (Leap: hand above sensor; webcam/mock: confident presence), `adapt_frame(frame)`
  (re-mapping seam), `prompts()`.

### Paradigm — `motor_tests/base_test.py:BaseMotorTest`
```python
get_instructions() -> str
get_live_metric(frame) -> float        # one number for the live plot
get_live_metric_label() -> str
compute_features() -> dict[str, float] # final analysis from collected frames
test_type() -> str                     # canonical key
start()/stop()                         # records via capture.start_recording(_on_frame)
```
- `_on_frame` routes each frame through `SourceProfile.adapt_frame` (the seam).

### Registry — `motor_tests/registry.py` (single source of truth)
`PARADIGMS: list[ParadigmSpec]` declares each paradigm once: `key, label, updrs,
description, category (MOTOR|COGNITIVE), bilateral, sim_scenario, screen,
cls_path, cls_kwargs`. The dashboard, the main-window router, storage
categorisation and the capability gating **all derive from it** (previously these
were three drifting lists).

---

## 3. Naming scheme

| Concept | Canonical name | Notes / deprecated aliases |
|---|---|---|
| Input/hardware abstraction | **Source** (`MotionSource`) | alias `BaseCaptureDevice` (Stage-2 removal) |
| Source factory | `create_source(kind)` | alias `create_capture_device` |
| Source kinds | `"leap"`, `"webcam"`, `"mock"` | `"mediapipe"`→`webcam`, `"sim"`→`mock` normalized |
| Simulation source | `SimulationSource` | alias `MockCaptureDevice` |
| Per-source metadata | `SourceProfile` | capabilities/readiness/prompts |
| Data model (now) | `HandFrame` / `FingerData` / `BoneData` | — |
| Data model (Stage 2) | `HandPose` + `TrackingFrame` envelope | `TrackingFrame(hands[], face?, gaze?)` |
| Task | **Paradigm** (`ParadigmSpec`, registry) | class still `BaseMotorTest` until Stage 2 |
| Category | `Category.MOTOR` / `Category.COGNITIVE` | value = DB `CATEGORY_CHAR` |
| Pre-test gate | `ReadinessGate` | source-agnostic hand model + 1-2-3 |

UI keeps the user-facing word **"Tracking"** (Tracking screen / button); internally
the layer is **Source**.

---

## 4. Critique of the pre-consolidation construct (addressed / pending)

- ✅ **Naming drift** (capture/device/source/sensor/tracking/mode) — unified on
  *Source* with backward-compatible aliases.
- ✅ **Triple registration** of paradigms — collapsed into `motor_tests/registry.py`.
- ✅ **Category computed by a function** — now declared on `ParadigmSpec.category`.
- ✅ **Simulation not contract-tested** — `SimulationSource` is first-class and
  `tests/test_paradigm_contracts.py` drives every paradigm through it.
- ⚠️ **Hand-only frame callback** (`Callable[[HandFrame], None]`) — the structural
  blocker for face/oculomotor. Resolved in Stage 2 by the `TrackingFrame` envelope.
- ⚠️ **`motor_tests/` package holds cognitive paradigms too** — rename to
  `paradigms/` in Stage 2.
- ⚠️ **Bilateral asymmetry** — unilateral paradigms get one frame per callback,
  bilateral get two; the `TrackingFrame` envelope (one frame carries both hands)
  makes this symmetric.
- ◻️ **gesture_lab** is a parallel feature pipeline sharing `HandFrame`; long term
  it becomes a paradigm category.

---

## 5. Multimodal plug-in points (face / oculomotor)

The sidecar protocol (`mediapipe_sidecar/PROTOCOL.md`) is already message-typed,
so a `{"type":"face", ...}` stream (MediaPipe Face Landmarker: 478 landmarks +
iris + 52 blendshapes) is additive. To add a modality:

1. **Sidecar**: load `FaceLandmarker` alongside `HandLandmarker`; emit `face` msgs.
2. **Source**: dispatch `face` → `FacePose`/`GazePose` (new dataclasses) on a new
   consumer; add `CAP_FACE_LANDMARKS` / `CAP_EYE_GAZE` capability tokens.
3. **Data model**: `TrackingFrame{ hands: list[HandPose], face, gaze }` — the
   single callback type that makes modalities uniform.
4. **Registry**: add `Category.OCULAR` / `Category.FACIAL`; new `ParadigmSpec`s
   (saccades, smooth pursuit, anti-saccade, facial-expression battery).
5. **Storage**: new `CONCEPT_DIMENSION` rows (`TAPPD:…`/`MOTRYX:…`).

---

## 6. Staged roadmap

**Stage 1 (done)** — contracts + canonical names (aliased), single paradigm
registry, first-class `SimulationSource`, end-to-end paradigm contract tests,
this document.

**Stage 2 — in progress**
- ✅ Data-model: `HandPose` + `TrackingFrame` envelope introduced (additive;
  `HandFrame` aliases `HandPose`). The callback is still per-`HandPose` — the
  full `Callable[[TrackingFrame], None]` migration across the ~34 consumers is
  the remaining step.
- ✅ Concrete sources renamed: `LeapSource`/`WebcamSource`/`SimulationSource`
  (old names kept as aliases).
- ✅ Capture-source **provenance** saved with every measurement (raw JSON +
  `OBSERVATION_BLOB.source_kind`) and shown in results (⚠ flags simulation).
- ✅ **App rename → Motryx**: title, `QSettings` org/app via `app_settings.py`
  with a one-time TapPD→Motryx migration shim.
- ◻️ Remaining: migrate consumers to the `TrackingFrame` callback; drop the
  backward-compat aliases; rename package `motor_tests/ → paradigms/`; add
  `OCULAR`/`FACIAL` categories; multimodal sidecar `FaceLandmarker` +
  `FacePose`/`GazePose` + oculomotor paradigms; assets/icon + README rename.
