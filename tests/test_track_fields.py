"""The overlay bookkeeping the mapping attaches to each HandFrame."""

from capture.mediapipe_mapping import frames_from_message


def _world():
    return [[0.01 * i, 0.02 * i, 0.0] for i in range(21)]


def test_frame_index_image_landmarks_and_iris_are_attached():
    img = [[0.1, 0.2]] * 21
    msg = {"type": "hand", "ts": 1000, "frame": 42, "w": 640, "h": 480,
           "iris_px": [[256.0, 120.0], [384.0, 120.0]], "iris_age_ms": 10,
           "hands": [{"handedness": "Left", "score": 0.9, "world": _world(),
                      "palm_px": [300.0, 200.0], "image": img}]}

    (f,) = frames_from_message(msg)

    assert f.frame_index == 42
    assert f.image_landmarks == img
    assert f.iris_norm == [[0.4, 0.25], [0.6, 0.25]]


def test_fields_are_none_when_the_sidecar_did_not_send_them():
    msg = {"type": "hand", "ts": 1000,
           "hands": [{"handedness": "Right", "score": 1.0, "world": _world()}]}

    (f,) = frames_from_message(msg)

    assert f.frame_index is None and f.image_landmarks is None and f.iris_norm is None
