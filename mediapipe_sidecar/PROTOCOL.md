# Sidecar protocol

Newline-delimited JSON over a local TCP socket (or stdin/stdout in `--stdio`
debug mode). The **main TapPD app is the socket server** and passes its port to
the sidecar via `--port`; the sidecar connects back. Messages are typed so new
modalities (face/eye) can be added without breaking changes.

## Main app → sidecar (commands)

| Message | Meaning |
|---|---|
| `{"cmd":"list_cameras"}` | enumerate cameras |
| `{"cmd":"config","preview_fps":15,"preview_max_width":640,"jpeg_quality":70,"hand_confidence":0.5,"tracking_confidence":0.5,"record_fps":30,"record_codec":"avc1"}` | set tunables (sent on connect from `capture/capture.yaml`; all keys optional). Apply before `start` so the landmarker picks up the confidences |
| `{"cmd":"start","index":0}` | open camera `index` and begin streaming `hand` frames |
| `{"cmd":"start","video":"/path.mp4"}` | open a video file instead of a camera (default: loop forever) |
| `{"cmd":"start","video":"/path.mp4","start_s":2.0,"end_s":7.5,"loop":false}` | play only `[start_s,end_s]` once, then emit `done` (VideoLab). `start_s`/`end_s`/`loop` are optional; no range + `loop:true` (default) = legacy looping |
| `{"cmd":"stop"}` | stop streaming, keep source warm |
| `{"cmd":"preview","on":true}` | enable/disable the throttled `preview` JPEG stream |
| `{"cmd":"face","on":true}` | enable/disable the (optional) face landmarker in the preview |
| `{"cmd":"record","path":"/clip.mp4","seconds":10}` | record live frames to an mp4 for `seconds` |
| `{"cmd":"quit"}` | stop and exit |

## Sidecar → main app (events)

```jsonc
// reply to list_cameras
{"type":"cameras","items":[{"index":0,"name":"OBSBOT Tiny"}]}

// per processed frame (full rate) — raw MediaPipe world landmarks (meters)
{"type":"hand","ts":1719_650_000_000,"hands":[
  {"handedness":"Right","score":0.98,
   "world":[[x,y,z], ...21 points...]}        // hand_world_landmarks, metres
]}

// throttled (~15 fps) — only while preview is on
{"type":"preview","jpeg":"<base64>","w":640,"h":360,
 "landmarks":[[[x,y], ...21...]],             // normalized image coords, per hand
 "face":[[x,y], ...478...]}                   // first face, normalized; 468-477 = iris/eyes

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
- `handedness` is from the image's perspective; front-facing webcams mirror, so
  the main app exposes a left/right flip setting.

## Future (not implemented)

A `{"type":"face", ...}` message (MediaPipe Face Landmarker: 478 landmarks +
iris + 52 blendshapes) is an additive extension — no protocol change needed.
