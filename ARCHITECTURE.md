# Architecture

> **Motryx** (motor + metrics) — a multimodal movement-assessment lab (hand
> tracking now; face & oculomotor on the roadmap). Formerly *TapPD* (still the
> DB concept namespace; see Storage).

This document defines the **layer contracts** (the standard API each layer
agrees on), the naming scheme, the configuration story, and the staged plan
toward multimodal support. The component-by-component map of the whole system
(directories, data flows, storage topology, per-component analysis) lives in
**[BLUEPRINT.md](BLUEPRINT.md)** — start there for orientation.

---

## 1. Layering overview

```
┌──────────────┐   frames    ┌───────────────┐  features  ┌──────────────┐
│   SOURCE     │ ──────────► │   PARADIGM    │ ─────────► │   STORAGE    │
│ capture/     │  callback   │ paradigms/  │            │ storage/ +   │
└──────────────┘             └───────────────┘            │ video/store  │
       ▲   ▲                    ▲        ▲                 └──────────────┘
       │   │ SourceProfile      │        │ ParadigmRunner (shared frame-pump)
       │   │ (caps, readiness)  │ Readiness│
       │   │                    │  Gate    │
   ┌───┴───────────┐            │          │
   │ VIDEO service │────────────┘   UI: ui/ (TestScreen, VideoLab, Eingabequelle)
   │ video/        │   clips/playback        shared widgets: WebcamPreview,
   └───────────────┘                          LiveMetricPlot
```

- **Source** (`capture/`) — produces motion data (HandFrames). One abstraction,
  several implementations (Leap, webcam-via-sidecar, simulation, replay).
  Hot-swappable at runtime via the Eingabequelle screen.
- **Video service** (`video/`) — owns video *media*: record, import, transcode,
  per-segment extraction (+ defacing/eye-ref), clip storage+metadata, and
  playback orchestration. Used by **both** the Sim source (Eingabequelle) and
  **VideoLab**. Sits beside `capture/` and feeds clip paths to a `WebcamSource`
  for playback (`ui → video → capture`).
- **Paradigm** (`paradigms/`) — a clinical/cognitive task that consumes frames
  and computes features. Declared once in the **registry**. Driven by the shared
  **`ParadigmRunner`** (frame intake + gating + live-metric + buffers), used by
  both the live `TestScreen` and VideoLab's `AnalysisRunner`.
- **Storage** — i2b2-style star schema (live results) + JSON `VideoSession`
  store (VideoLab; write-isolated, per-segment export into a dedicated DB
  Session via `video/export.py`).
- **SourceProfile** — per-source capabilities, readiness policy and prompts; the
  seam where source-specific frame re-mapping plugs in (`adapt_frame`).

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

### Paradigm — `paradigms/base_test.py:BaseParadigm`
```python
get_instructions() -> str
get_live_metric(frame) -> float        # one number for the live plot
get_live_metric_label() -> str
compute_features() -> dict[str, float] # final analysis from collected frames
test_type() -> str                     # canonical key
start()/stop()                         # records via capture.start_recording(_on_frame)
```
- `_on_frame` routes each frame through `SourceProfile.adapt_frame` (the seam).

### Registry — `paradigms/registry.py` (single source of truth)
`PARADIGMS: list[ParadigmSpec]` declares each paradigm once: `key, label, updrs,
description, category (MOTOR|COGNITIVE), bilateral, sim_scenario, screen,
cls_path, cls_kwargs`. The dashboard, the main-window router, storage
categorisation and the capability gating **all derive from it**.

### ParadigmRunner — `paradigms/runner.py` (shared frame-pump)
`begin()` resets the paradigm + live buffers; `feed(frame)` gates (optional
SETTLE + duration for live; none when `sidecar_bounded`), calls `test._on_frame`,
computes the live metric, and stashes per-hand `live`/`last_frame`. Both
`ui/test_screen.py` and `ui/analysis_runner.py` (VideoLab) use it — no duplicated
callback glue.

---

## 2b. Video service — `video/` + `mediapipe_sidecar/`

The Sim source (record a clip → loop it through MediaPipe) and VideoLab (import a
video → run a bounded range through MediaPipe) are the **same concept**, so the
video media responsibilities live in one place:

- `video/clip.py` — `VideoClip` (mp4 + sidecar `.meta.json`: duration/fps/res/
  provenance/`deidentified`) and `VideoLibrary` (the Sim/global `data/clips/`).
- `video/recorder.py` / `video/importer.py` — produce a `VideoClip` from a live
  recording (sidecar `record`) or an imported file (sidecar transcode). Both end
  at the same metadata.
- `video/extractor.py` (+ `mediapipe_sidecar/extract.py`) — cut a segment
  `[start,end]` → a compact clip, optionally **defacing** the face
  (`privacy.deface: blur|mesh|off`) and saving a privacy-safe **eye-reference
  track** (`<clip>.eyeref.json`: iris centres only) so tremor's absolute-position
  recovery survives defacing.
- `video/transcode.py` (+ `mediapipe_sidecar/transcode.py`) — normalize imports
  (H.264, square-capped resolution so portrait clips keep width, fps cap,
  rotation-aware).
- `video/store.py` — `VideoSession` (per-patient segments + results), JSON.
- **Playback** is `WebcamSource.play_range(video, start, end)` / `replay_path`
  loop → the sidecar (`start` with `video`/`start_s`/`end_s`/`loop`, emits `done`
  for a play-once range).

The main app (Py3.14) has **no cv2**; all cv2/MediaPipe work runs in the Py3.12
sidecar venv (looping playback in-process; transcode/extract as one-shot
subprocesses). Video *display/scrubbing* uses Qt Multimedia (`QMediaPlayer`).

---

## 2c. Configuration (YAML-first)

Per-domain YAML, loaded via the shared `config_loader.py` (defaults deep-merged
with the file):

- `capture/capture.yaml` (`capture/config.py`) — sidecar tunables (preview fps /
  jpeg quality / detection confidences / record fps+codec), readiness thresholds,
  preview staleness. The main app reads these and **pushes them to the sidecar
  over the socket** (`{"cmd":"config",...}` on connect; the sidecar venv has no
  pyyaml).
- `video/video.yaml` (`video/config.py`) — import format/resolution/fps, segment
  extraction + privacy (deface, eye-ref), Sim record durations, analysis knobs.
- `paradigms/test_config.yaml` — per-paradigm signal-processing + features.

Remaining hardcoded (roadmap): `ui/theme.py` colours/sizes; `mock_capture.py`
simulation parameters.

---

## 3. Naming scheme

| Concept | Canonical name | Notes / deprecated aliases |
|---|---|---|
| Input/hardware abstraction | **Source** (`MotionSource`) | alias `BaseCaptureDevice` still kept |
| Source factory | `create_source(kind)` | — (`create_capture_device` removed) |
| Source kinds | `"leap"`, `"webcam"`, `"mock"` | `"mediapipe"`→`webcam`, `"sim"`→`mock`; `"replay"` (landmark json) |
| Leap / webcam / sim sources | `LeapSource` / `WebcamSource` / `SimulationSource` | concrete aliases (`*CaptureDevice`) **removed** |
| Per-source metadata | `SourceProfile` | capabilities/readiness/prompts |
| Data model (now) | `HandFrame` / `FingerData` / `BoneData` | `HandFrame` aliases `HandPose` |
| Data model (Stage 2) | `HandPose` + `TrackingFrame` envelope | `TrackingFrame(hands[], face?, gaze?)` |
| Task | **Paradigm** (`ParadigmSpec`, registry) | class still `BaseParadigm` |
| Frame-pump | `ParadigmRunner` | shared by TestScreen + VideoLab |
| Preview widget | `WebcamPreview` (`ui/widgets/`) | was a private class in tracking_screen |
| Live plot | `LiveMetricPlot` (`ui/widgets/`) | shared by TestScreen + VideoLab |
| Category | `Category.MOTOR` / `Category.COGNITIVE` | value = DB `CATEGORY_CHAR` |
| Pre-test gate | `ReadinessGate` | source-agnostic hand model + 1-2-3 |

The input-source screen is user-labelled **"Eingabequelle"**; internally the
layer is **Source**. Only `BaseCaptureDevice` and `HandFrame` aliases remain
(tied to the deferred `TrackingFrame` callback migration).

---

## 4. Critique of the pre-consolidation construct (addressed / pending)

- ✅ **Naming drift** (capture/device/source/sensor/tracking/mode) — unified on
  *Source* with backward-compatible aliases.
- ✅ **Triple registration** of paradigms — collapsed into `paradigms/registry.py`.
- ✅ **Category computed by a function** — now declared on `ParadigmSpec.category`.
- ✅ **Simulation not contract-tested** — `SimulationSource` is first-class and
  `tests/test_paradigm_contracts.py` drives every paradigm through it.
- ⚠️ **Hand-only frame callback** (`Callable[[HandFrame], None]`) — the structural
  blocker for face/oculomotor. Resolved in Stage 2 by the `TrackingFrame` envelope.
- ⚠️ **`paradigms/` package holds cognitive paradigms too** — rename to
  `paradigms/` in Stage 2.
- ⚠️ **Bilateral asymmetry** — unilateral paradigms get one frame per callback,
  bilateral get two; the `TrackingFrame` envelope (one frame carries both hands)
  makes this symmetric.
- ◻️ **gesture_lab** is a parallel feature pipeline sharing `HandFrame`; long term
  it becomes a paradigm category.

---

## 5. Multimodal plug-in points (face / oculomotor)

The sidecar protocol (`mediapipe_sidecar/PROTOCOL.md`) is already message-typed,
and the sidecar **already runs a `FaceLandmarker`** (478 landmarks incl. iris):
its output rides in the throttled `preview` message (`face` field, toggled via
`{"cmd":"face"}`), is consumed by the tracking screen's eye-reference overlay,
and drives defacing + the `.eyeref.json` track in `extract.py`. What's missing
is the full-rate modality stream + data model. To add a modality:

1. **Sidecar**: emit dedicated full-rate `face` msgs (landmarker already loaded).
2. **Source**: dispatch `face` → `FacePose`/`GazePose` (new dataclasses) on a new
   consumer; add `CAP_FACE_LANDMARKS` / `CAP_EYE_GAZE` capability tokens.
3. **Data model**: `TrackingFrame{ hands: list[HandPose], face, gaze }` — the
   single callback type that makes modalities uniform.
4. **Registry**: add `Category.OCULAR` / `Category.FACIAL`; new `ParadigmSpec`s
   (saccades, smooth pursuit, anti-saccade, facial-expression battery).
5. **Storage**: new `CONCEPT_DIMENSION` rows (`TAPPD:…`/`MOTRYX:…`).

---

## 6. Staged roadmap

**Stage 1 (done)** — contracts + canonical names, single paradigm registry,
first-class `SimulationSource`, end-to-end paradigm contract tests, this document.

**Stage 2 (done)** — `HandPose`/`TrackingFrame` introduced; concrete sources
renamed (`LeapSource`/`WebcamSource`/`SimulationSource`); provenance
(`source_kind`) saved per measurement; **app renamed → Motryx** (settings
migration shim).

**VideoLab + service consolidation (done)**
- **VideoLab** — import a phone video, select onset/offset segments, run a
  paradigm on each segment (overlay + realtime + result); per-segment compact
  clips with **defacing** + a privacy-safe **eye-reference track**; Sim source
  (record→loop) on the Eingabequelle screen.
- **Video service** (`video/`) unifies record/import/transcode/extract/store/
  playback for Sim + VideoLab.
- **Shared UI**: `WebcamPreview`, `LiveMetricPlot`, `ParadigmRunner` — the
  TestScreen↔VideoLab duplication (frame-pump, plot, overlay) is gone.
- **Config**: per-domain YAML (`capture.yaml` / `video.yaml`) + shared loader;
  sidecar tunables pushed over the socket. Readiness/staleness/record/codec/
  resolution are all config.
- **Aliases**: concrete `*CaptureDevice` + `create_capture_device` removed
  (`BaseCaptureDevice`/`HandFrame` remain, tied to the callback migration).

**Remaining / future**
- ~~Migrate consumers to the TrackingFrame callback~~ — done: `start_tracking`
  streams envelopes (all hands per sensor frame + `FacePose`); paradigms/
  TestScreen/VideoLab consume them. `BaseCaptureDevice`/`HandFrame` aliases
  remain for the per-hand convenience API (gesture lab, gates).
- ~~**Unlock tremor on video**~~ — done, live variant: the sidecar attaches
  iris centres (`iris_px`, ~5 Hz cadence) + per-hand `palm_px` to the full-rate
  `hand` stream; `mediapipe_mapping.eye_ref_position_mm` derives an absolute
  ≈mm palm position, promoted in `SourceProfile.adapt_frame` (webcam). The
  webcam gains `CAP_ABS_POSITION` dynamically while face tracking is on
  (`extra_capabilities`); VideoLab forces face on for abs-position paradigms
  and warns when the eye reference covered <50% of frames. The stored
  `.eyeref.json` track remains an (unconsumed) archive artifact for clips
  whose original is gone.
- ~~**Export** VideoLab segment results into patient Sessions~~ — done:
  `video/export.py` (one DB Session per video session, `source_kind="video"`,
  double-export guarded by the `measurement_id` stamp).
- ~~Rename package → `paradigms/`~~ done; ~~`OCULAR` category + `FacePose`
  paradigm~~ done (`ocular_fixation`: Fixationsstabilität, Blinkrate,
  sakkadische Intrusionen). Next: `FACIAL` (Blendshapes/Hypomimie), Sakkaden-/
  Pursuit-Paradigmen mit Stimulus-Screen, kalibrierte `GazePose`.
- YAML-ify `ui/theme.py` + `mock_capture.py` simulation params.
