"""One-shot video normalizer (runs under the sidecar venv, which has cv2).

Downscales to fit a max resolution (keeping aspect), caps the frame rate, and
re-encodes to a compact, widely-decodable mp4. Invoked as a blocking subprocess
by video/transcode.py — no socket protocol. Prints a single JSON line.

Orientation: OpenCV is asked to auto-apply the container's rotation metadata
(portrait phone clips), and dimensions are taken from the actually-decoded
frame so the result is always upright. An optional MANUAL rotation (degrees) is
applied on top, for the VideoLab "rotate" button when auto-detection is wrong.

Usage: transcode.py SRC DEST MAX_W MAX_H FPS_CAP [FOURCC] [ROTATE_DEG]
"""

import json
import sys

import cv2


def _rot_code(deg: int):
    return {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
            270: cv2.ROTATE_90_COUNTERCLOCKWISE}.get(deg % 360)


def main() -> int:
    src, dest = sys.argv[1], sys.argv[2]
    max_w, max_h, fps_cap = int(sys.argv[3]), int(sys.argv[4]), float(sys.argv[5])
    fourcc_str = sys.argv[6] if len(sys.argv) > 6 else "mp4v"
    manual_rot = int(sys.argv[7]) % 360 if len(sys.argv) > 7 else 0

    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        print(json.dumps({"ok": False, "error": f"Konnte Video nicht öffnen: {src}"}))
        return 1
    # Ask OpenCV to apply the container's rotation metadata (upright frames).
    try:
        cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)
    except Exception:
        pass

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if src_fps <= 0:
        src_fps = 30.0
    src_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    ok, first = cap.read()
    if not ok:
        print(json.dumps({"ok": False, "error": "Video lieferte keine Frames"}))
        return 1
    rc = _rot_code(manual_rot)
    if rc is not None:
        first = cv2.rotate(first, rc)
    h0, w0 = first.shape[:2]
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    scale = min(max_w / w0, max_h / h0, 1.0)
    w = max(2, int(round(w0 * scale)) & ~1)   # even dimensions
    h = max(2, int(round(h0 * scale)) & ~1)
    out_fps = min(src_fps, fps_cap) if fps_cap > 0 else src_fps
    keep_ratio = out_fps / src_fps             # frame-drop cadence to hit the fps cap

    writer = cv2.VideoWriter(dest, cv2.VideoWriter_fourcc(*fourcc_str), out_fps, (w, h))
    if not writer.isOpened():
        print(json.dumps({"ok": False, "error": "VideoWriter konnte nicht geöffnet werden"}))
        return 1

    acc = 0.0
    n = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        acc += keep_ratio
        if acc < 1.0:
            continue   # drop this frame to cap fps
        acc -= 1.0
        if rc is not None:
            frame = cv2.rotate(frame, rc)
        if frame.shape[1] != w or frame.shape[0] != h:
            frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
        writer.write(frame)
        n += 1

    cap.release()
    writer.release()
    if n == 0:
        print(json.dumps({"ok": False, "error": "Keine Frames geschrieben"}))
        return 1
    print(json.dumps({"ok": True, "path": dest, "w": w, "h": h,
                      "fps": round(out_fps, 3), "frames": n,
                      "src_w": w0, "src_h": h0, "src_fps": round(src_fps, 3),
                      "src_duration_s": round(src_frames / src_fps, 2) if src_fps else 0.0}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)
