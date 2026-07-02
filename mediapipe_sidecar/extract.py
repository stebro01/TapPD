"""Extract a [start_s, end_s] range → a compact mp4, optionally de-identifying
faces (privacy), and (optionally) saving an eye-reference track. Runs under the
sidecar venv (cv2 + mediapipe). Prints one JSON line.

Defacing is hand-aware: a HandLandmarker runs alongside the FaceLandmarker and
hand regions (dilated convex hull of the 21 landmarks) are excluded from the
blur/mesh — a hand held in front of the face keeps its pixels, so the stored
clip remains analysable. (The primary analysis runs on the original video
anyway; the clip is archive/review.)

Eye-reference track (privacy-preserving tremor support)
-------------------------------------------------------
Defacing removes the face *pixels*, so MediaPipe can no longer find the eyes in
the stored clip — yet tremor analysis needs the eyes to recover ABSOLUTE hand
position (inter-pupillary distance → mm-per-pixel scale; webcam/video give only
hand-relative coordinates otherwise). The fix: capture the eye reference HERE,
while the face is still visible, and store only the two **iris centre points**
per frame (MediaPipe landmarks 468 + 473) in a sidecar ``<dest>.eyeref.json``.
These are geometric coordinates, NOT a face image → not biometrically
identifying, so privacy is preserved while the abs-position capability is kept.
The (future) tremor adapter reads this track instead of re-detecting the (now
blurred) face. The face landmarker runs if EITHER defacing OR eye-ref capture is
requested, so the per-frame detection is shared.

Usage: extract.py SRC DEST START_S END_S MAX_W MAX_H FPS_CAP FOURCC DEFACE BLUR EYEREF
  DEFACE: off | blur | mesh   BLUR: gaussian kernel for "blur"   EYEREF: 1 | 0
"""

import json
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
FACE_MODEL = os.path.join(HERE, "models", "face_landmarker.task")
HAND_MODEL = os.path.join(HERE, "models", "hand_landmarker.task")
_IRIS_L, _IRIS_R = 468, 473        # MediaPipe iris-centre landmark indices
_AVG_IPD_MM = 63.0                 # average human inter-pupillary distance


def _strip_appledouble() -> None:
    # Non-native volumes spawn ._* files that break matplotlib's stylelib glob
    # during `import mediapipe`. Same self-heal the sidecar does.
    try:
        import matplotlib
        d = os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data", "stylelib")
        for name in os.listdir(d):
            if name.startswith("._"):
                try:
                    os.remove(os.path.join(d, name))
                except OSError:
                    pass
    except Exception:
        pass


def _make_face_landmarker():
    if not os.path.isfile(FACE_MODEL):
        return None
    _strip_appledouble()
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision
    base = mp_python.BaseOptions(model_asset_path=FACE_MODEL)
    opts = mp_vision.FaceLandmarkerOptions(
        base_options=base, running_mode=mp_vision.RunningMode.IMAGE, num_faces=1)
    return mp, mp_vision.FaceLandmarker.create_from_options(opts)


def _make_hand_landmarker():
    if not os.path.isfile(HAND_MODEL):
        return None
    _strip_appledouble()
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision
    base = mp_python.BaseOptions(model_asset_path=HAND_MODEL)
    opts = mp_vision.HandLandmarkerOptions(
        base_options=base, running_mode=mp_vision.RunningMode.IMAGE, num_hands=2)
    return mp_vision.HandLandmarker.create_from_options(opts)


def _hand_exclude_mask(frame_shape, hands_landmarks):
    """255 where a hand is (dilated convex hull of the 21 landmarks), else 0.

    A hand held in front of the face must NOT be defaced — blurring it would
    destroy the very motion signal the segment exists to measure.
    """
    import numpy as np
    h, w = frame_shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    for lms in hands_landmarks:
        pts = np.array([[int(lm.x * w), int(lm.y * h)] for lm in lms], dtype=np.int32)
        cv2.fillConvexPoly(mask, cv2.convexHull(pts), 255)
    if mask.any():
        k = max(5, (int(min(h, w) * 0.05) | 1))   # generous margin around the hand
        mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    return mask


def _deface(frame, landmarks, mode: str, blur: int, exclude_mask=None):
    h, w = frame.shape[:2]
    xs = [lm.x for lm in landmarks]
    ys = [lm.y for lm in landmarks]
    x0, x1 = int(max(0, min(xs)) * w), int(min(1.0, max(xs)) * w)
    y0, y1 = int(max(0, min(ys)) * h), int(min(1.0, max(ys)) * h)
    px, py = int((x1 - x0) * 0.15), int((y1 - y0) * 0.22)   # expand to cover hair/chin
    x0, x1 = max(0, x0 - px), min(w, x1 + px)
    y0, y1 = max(0, y0 - py), min(h, y1 + py)
    if x1 <= x0 or y1 <= y0:
        return frame
    roi = frame[y0:y1, x0:x1]
    keep = None   # hand pixels inside the face box that must survive
    if exclude_mask is not None:
        m = exclude_mask[y0:y1, x0:x1]
        if m.any():
            keep = (m, roi.copy())
    if mode == "blur":
        k = max(11, (blur | 1))   # odd kernel
        frame[y0:y1, x0:x1] = cv2.GaussianBlur(roi, (k, k), 0)
    elif mode == "mesh":
        cv2.rectangle(frame, (x0, y0), (x1, y1), (0, 0, 0), -1)
        for lm in landmarks:
            cv2.circle(frame, (int(lm.x * w), int(lm.y * h)), 1, (140, 230, 120), -1)
        for i in range(468, min(478, len(landmarks))):   # iris
            cv2.circle(frame, (int(landmarks[i].x * w), int(landmarks[i].y * h)), 2, (180, 64, 255), -1)
    if keep is not None:
        m, orig = keep
        region = frame[y0:y1, x0:x1]
        region[m > 0] = orig[m > 0]
        frame[y0:y1, x0:x1] = region
    return frame


def main() -> int:
    src, dest = sys.argv[1], sys.argv[2]
    start_s, end_s = float(sys.argv[3]), float(sys.argv[4])
    max_w, max_h, fps_cap = int(sys.argv[5]), int(sys.argv[6]), float(sys.argv[7])
    fourcc = sys.argv[8] if len(sys.argv) > 8 else "mp4v"
    deface = sys.argv[9] if len(sys.argv) > 9 else "off"
    blur = int(sys.argv[10]) if len(sys.argv) > 10 else 35
    capture_eyeref = (sys.argv[11] == "1") if len(sys.argv) > 11 else True

    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        print(json.dumps({"ok": False, "error": f"Konnte Video nicht öffnen: {src}"}))
        return 1
    try:
        cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)
    except Exception:
        pass
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if fps <= 0:
        fps = 30.0
    start_f = max(0, round(start_s * fps))
    end_f = round(end_s * fps) if end_s > 0 else None

    ok, first = cap.read()
    if not ok:
        print(json.dumps({"ok": False, "error": "Video lieferte keine Frames"}))
        return 1
    h0, w0 = first.shape[:2]
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)
    scale = min(max_w / w0, max_h / h0, 1.0)
    w = max(2, int(round(w0 * scale)) & ~1)
    h = max(2, int(round(h0 * scale)) & ~1)
    out_fps = min(fps, fps_cap) if fps_cap > 0 else fps
    keep = out_fps / fps

    writer = cv2.VideoWriter(dest, cv2.VideoWriter_fourcc(*fourcc), out_fps, (w, h))
    if not writer.isOpened():
        print(json.dumps({"ok": False, "error": "VideoWriter konnte nicht geöffnet werden"}))
        return 1

    do_deface = deface in ("blur", "mesh")
    need_face = do_deface or capture_eyeref
    mp = fl = hl = None
    if need_face:
        made = _make_face_landmarker()
        if made is not None:
            mp, fl = made
    if do_deface and fl is not None:
        hl = _make_hand_landmarker()   # hands must survive the deface (exclude mask)
    has_eyeref = capture_eyeref and fl is not None
    iris_track = [] if has_eyeref else None   # per output frame: [[xL,yL],[xR,yR]] or None

    acc = 0.0
    n = 0
    idx = start_f
    while True:
        if end_f is not None and idx >= end_f:
            break
        ok, frame = cap.read()
        if not ok:
            break
        idx += 1
        acc += keep
        if acc < 1.0:
            continue
        acc -= 1.0
        if scale < 1.0:
            frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
        if fl is not None:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = fl.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
            lms = res.face_landmarks[0] if res.face_landmarks else None
            if has_eyeref:
                # Record only the two iris centres (normalized) — geometry, not a
                # face image; enough to recover the IPD scale for abs position.
                if lms is not None and len(lms) > _IRIS_R:
                    iris_track.append([[lms[_IRIS_L].x, lms[_IRIS_L].y],
                                       [lms[_IRIS_R].x, lms[_IRIS_R].y]])
                else:
                    iris_track.append(None)
            if do_deface and lms is not None:
                exclude = None
                if hl is not None:
                    hres = hl.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
                    if hres.hand_landmarks:
                        exclude = _hand_exclude_mask(frame.shape, hres.hand_landmarks)
                frame = _deface(frame, lms, deface, blur, exclude)
        writer.write(frame)
        n += 1
        if n == 1:   # first (defaced) frame → thumbnail for the work area
            cv2.imwrite(dest + ".thumb.jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])

    cap.release()
    writer.release()
    if n == 0:
        print(json.dumps({"ok": False, "error": "Keine Frames geschrieben"}))
        return 1

    wrote_eyeref = False
    if has_eyeref and any(e is not None for e in iris_track):
        with open(dest + ".eyeref.json", "w", encoding="utf-8") as f:
            json.dump({"fps": round(out_fps, 3), "frames": n, "w": w, "h": h,
                       "avg_ipd_mm": _AVG_IPD_MM, "iris": iris_track}, f)
        wrote_eyeref = True

    print(json.dumps({"ok": True, "path": dest, "w": w, "h": h, "fps": round(out_fps, 3),
                      "frames": n, "deidentified": do_deface, "eyeref": wrote_eyeref,
                      "thumb": dest + ".thumb.jpg"}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)
