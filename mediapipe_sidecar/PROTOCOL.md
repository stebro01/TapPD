# Sidecar protocol

Newline-delimited JSON over a local TCP socket (or stdin/stdout in `--stdio`
debug mode). The **main TapPD app is the socket server** and passes its port to
the sidecar via `--port`; the sidecar connects back. Messages are typed so new
modalities (face/eye) can be added without breaking changes.

## Main app → sidecar (commands)

| Message | Meaning |
|---|---|
| `{"cmd":"list_cameras"}` | enumerate cameras |
| `{"cmd":"config","preview_fps":15,"preview_max_width":640,"jpeg_quality":70,"hand_confidence":0.5,"tracking_confidence":0.5,"num_hands":2,"record_fps":30,"record_codec":"avc1","camera_width":0,"camera_height":0,"camera_fps":0,"mirror":true}` | set tunables (sent on connect from `capture/capture.yaml`; all keys optional). Apply before `start` so the landmarker picks up the confidences |
| | `camera_width`/`camera_height`/`camera_fps`: requested capture format, `0` = leave it to the driver. A camera that cannot serve the request silently substitutes a nearby mode; the negotiated format is logged to stderr |
| | `mirror`: mirror the **live camera** (selfie view). Video replay is not covered here — a clip brings its own flag with `start` |
| `{"cmd":"start","index":0}` | open camera `index` and begin streaming `hand` frames |
| `{"cmd":"start","video":"/path.mp4","mirror":true}` | `mirror` applies to **this clip only** (default `false`); whether a recording is the wrong way round depends on the device that made it, so it is decided per video, not globally |
| `{"cmd":"start","video":"/path.mp4"}` | open a video file instead of a camera (default: loop forever) |
| `{"cmd":"start","video":"/path.mp4","start_s":2.0,"end_s":7.5,"loop":false}` | play only `[start_s,end_s]` once, then emit `done` (VideoLab). `start_s`/`end_s`/`loop` are optional; no range + `loop:true` (default) = legacy looping |
| `{"cmd":"stop"}` | stop streaming, keep source warm |
| `{"cmd":"preview","on":true}` | enable/disable the throttled `preview` JPEG stream |
| `{"cmd":"face","on":true,"rate":"eco"}` | face landmarker on/off; `rate`: `"eco"` (~5 Hz eye reference) or `"full"` (every frame → dedicated `face` stream for ocular paradigms) |
| `{"cmd":"record","path":"/clip.mp4","seconds":10}` | record live frames to an mp4 for `seconds` |
| `{"cmd":"quit"}` | stop and exit |

## Sidecar → main app (events)

```jsonc
// reply to list_cameras
{"type":"cameras","items":[{"index":0,"name":"OBSBOT Tiny"}]}

// per processed frame (full rate) — raw MediaPipe world landmarks (meters).
// palm_px: palm centre in image PIXELS (wrist + 5 MCPs centroid).
// iris_px/iris_age_ms (only while face is on): last known iris centres in
// PIXELS + their age — the eye reference that makes the hand position
// absolute in the main app (IPD 63 mm → mm-per-pixel; unlocks tremor).
{"type":"hand","ts":1719_650_000_000,
 "iris_px":[[xL,yL],[xR,yR]],"iris_age_ms":120,
 "hands":[
  {"handedness":"Right","score":0.98,
   "world":[[x,y,z], ...21 points...],        // hand_world_landmarks, metres
   "palm_px":[px,py]}
]}

// throttled (~15 fps) — only while preview is on
{"type":"preview","jpeg":"<base64>","w":640,"h":360,
 "landmarks":[[[x,y], ...21...]],             // normalized image coords, per hand
 "face":[[x,y], ...478...]}                   // first face, normalized; 468-477 = iris/eyes

// per face detection (eco: ~5 Hz, full: every frame) — eye-centric subset
{"type":"face","ts":1719_650_000_000,
 "iris_px":[[xL,yL],[xR,yR]],                 // iris centres, PIXELS
 "corners_px":[[[xo,yo],[xi,yi]], [[...],[...]]],  // eye corners L/R (outer, inner)
 "ear":[0.31,0.30],                           // eye aspect ratio L/R (blink)
 "w":640,"h":360}

// recording finished (reply to record)
{"type":"recorded","path":"/clip.mp4"}

// a play-once range (loop:false) reached its offset or EOF — authoritative stop.
// Emitted exactly once; never emitted in looping mode. The capture thread then
// idles warm so a re-run / new range restarts cheaply.
{"type":"done"}

// any error (camera open failed, model missing, etc.)
{"type":"error","msg":"..."}
```

## Notes

- `world` are MediaPipe `hand_world_landmarks`: real-world **metres**, origin at
  the hand's geometric centre, hand-relative (absolute room position is NOT
  recoverable from RGB). The main app scales ×1000 → mm and maps to `HandFrame`
  in `capture/mediapipe_mapping.py`.
- 21-landmark index order: 0 wrist; 1–4 thumb (CMC, MCP, IP, TIP); 5–8 index
  (MCP, PIP, DIP, TIP); 9–12 middle; 13–16 ring; 17–20 pinky.
- `handedness` is reported as seen in the image the model was given — so when a
  frame is mirrored the label flips with it. The sidecar therefore swaps the
  label back whenever it mirrors (`_handedness()`), reading the same flag it
  flips on: what arrives here is always the anatomical side, and mirroring can
  never disagree with the label.
- On top of that the main app has an *additional* left/right swap
  (`flip_handedness`), for cameras that already mirror in hardware. It is off
  by default and does not belong to this protocol.
- Clips recorded via `record` store the **unmirrored** camera view, so a clip
  replays exactly like the live camera when `start` carries `mirror:true`.

## Face landmarks

The sidecar runs a Face Landmarker (478 landmarks incl. iris) alongside the
hand tracker. Three delivery paths:
- **`preview.face`** — full landmark set in the throttled preview (display).
- **`hand.iris_px`** — last iris centres on every hand message (eye reference
  for absolute hand position / tremor).
- **`{"type":"face"}`** — dedicated eye-centric stream (iris, corners, EAR)
  at eco (~5 Hz) or full rate; ocular paradigms consume this.
The 52 blendshapes (facial-expression battery) remain an additive future
extension — no protocol change needed.
