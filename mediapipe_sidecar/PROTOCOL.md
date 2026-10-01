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
| `{"cmd":"start","video":"/path.mp4","realtime":false}` | replay as fast as MediaPipe allows instead of at the clip's fps (analysis nobody watches). Default `true` |
| `{"cmd":"start","video":"/path.mp4","track":false}` | decode and stream `preview` frames only — **no** hand/face inference, no `hand` messages. For replaying an archived take under a *stored* overlay track. Default `true` |
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

// sent once per video pass, before its first frame: the clip's frame rate as
// OpenCV reports it (the time base of every `ts` of the pass), the file's
// frame count (0 = unknown) and the range's frame bounds (end_frame
// exclusive, null = to EOF)
{"type":"video","fps":30.0,"frames":241,"start_frame":15,"end_frame":225}

// per processed frame (full rate) — raw MediaPipe world landmarks (meters).
// ts: µs — video: media time, frame index / fps (see Notes); live: wall clock.
// palm_px: palm centre in image PIXELS (wrist + 5 MCPs centroid).
// iris_px/iris_age_ms (only while face is on): last known iris centres in
// PIXELS + their age (same time base as ts) — the eye reference that makes
// the hand position absolute in the main app (IPD 63 mm → mm-per-pixel;
// unlocks tremor).
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

// recording finished (reply to record): the file's facts, kept by the app as
// the take's provenance
{"type":"recorded","path":"/clip.mp4","w":640,"h":480,"fps":30.0,"frames":600,"codec":"avc1"}

// sent once right after connecting: what is doing the tracking
{"type":"hello","mediapipe":"1.0.1","opencv":"4.12.0","python":"3.12.10"}

// a play-once range (loop:false) reached its offset or EOF — authoritative stop.
// Emitted exactly once; never emitted in looping mode. The capture thread then
// idles warm so a re-run / new range restarts cheaply.
// frames/last_frame: what was delivered; eof: the file ended (or failed to
// decode) before end_frame; pts_drift_ms: largest drift of the container PTS
// against index/fps — above a frame interval the clip is not constant-rate
{"type":"done","frames":210,"first_frame":15,"last_frame":224,"end_frame":225,
 "eof":false,"pts_drift_ms":0.0}

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
- Every `hand` and `preview` message carries `frame` (0-based index into the
  source; for a camera a running count), and `hand` messages carry `w`/`h`
  (frame size in pixels) plus, per hand, `image`: the 21 landmarks in
  normalized image coordinates. Together these let the main app keep a
  per-frame track of an analysis and draw it over the archived clip later
  (`track:false` replay), instead of re-detecting — which a defaced archive
  could not support anyway.
- `handedness` is reported as seen in the image the model was given — so when a
  frame is mirrored the label flips with it. The sidecar therefore swaps the
  label back whenever it mirrors (`_handedness()`), reading the same flag it
  flips on: what arrives here is always the anatomical side, and mirroring can
  never disagree with the label.
- On top of that the main app has an *additional* left/right swap
  (`flip_handedness`), for cameras that already mirror in hardware. It is off
  by default and does not belong to this protocol.
- `preview` messages of a video replay carry `frame` (index in the file) and
  `t` (seconds into the file), so the app can hand a position between its own
  player and the sidecar replay (the review overlay starts where the player
  was and gives the position back when switched off).
- `start` with `start_s` and `loop:false` plays from that offset to the end
  once; the app then restarts without a range to continue from the top.
- **Time base.** `ts` of `hand` and `face` messages is µs. A live camera
  stamps the wall clock. A video stamps **media time**: frame index × 10⁶ /
  fps (the fps of the `video` message), i.e. the frame's position in the file
  — so a replay faster than real time (`realtime:false`) or slower (a big
  clip on a slow CPU) gives the same axis. A looping replay keeps counting
  across the wrap, so `ts` never goes backwards while `frame` restarts. The
  face cadence (eco = every round(0.2·fps) frames) and `iris_age_ms` run on
  the same clock; the hand and face message of one frame carry the same `ts`.
  Media time assumes a constant frame rate; `done.pts_drift_ms` reports how
  far the container's own timestamps disagree.
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
