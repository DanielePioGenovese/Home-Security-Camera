# /// script
# dependencies = [
#   "flask",
#   "opencv-python",
#   "ultralytics",
# ]
# ///
"""
Low-latency webcam streaming to a web page using Flask + OpenCV.

Fixes over the naive version:
  1. Latency: a background thread keeps ONLY the latest frame, so old frames
     are dropped instead of queuing up and arriving late in the browser.
  2. Wasted bandwidth: the HTTP generator sends a frame only when a NEW one
     is available, and is capped at MAX_FPS. Otherwise it would re-send the
     same frame hundreds of times per second and saturate the connection.
  3. Startup crash: the camera is opened lazily, in the background. If it
     can't be opened, the web server still starts and the page shows a clear
     error instead of the whole app failing to boot.

Run with uv (inline deps above are used, no manual venv/install needed):
    uv run webcam_stream.py

Then open in your browser:
    http://localhost:5000
"""

import threading
import time

import cv2
from flask import Flask, Response, jsonify
from ultralytics import YOLO

app = Flask(__name__)

# Loaded once at startup (a few seconds) so per-frame inference is fast.
YOLO_MODEL = YOLO("yolo26n.pt")

# Fraction of the frame that must be "moving" (per background subtraction)
# before YOLO runs on it. Running YOLO on every single frame would be far
# too slow for a live stream; gating on motion keeps idle frames cheap.
MOTION_THRESHOLD = 0.01

# Which camera to use. Either:
#   - an integer index: 0, 1, 2, ...
#   - a V4L2 device path on Linux: "/dev/video2"
#   - None: auto-detect the first index that actually returns frames
#
# NOTE on Linux: one physical webcam often exposes several /dev/videoN nodes,
# and some of them are metadata-only (they open, but never return a frame).
# Using an external camera.
CAMERA_SOURCE = 2

# If auto-detecting, how many indices to probe.
MAX_INDEX_TO_PROBE = 10

# Lower resolution / JPEG quality = less data to encode and send = less lag.
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
JPEG_QUALITY = 80  # 0-100, lower = faster encode, smaller frames
MAX_FPS = 30  # upper bound on frames sent to the browser

def open_capture(source):
    """Open a VideoCapture and verify it actually delivers a frame."""
    capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        capture.release()
        return None

    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

    # A node can "open" successfully and still never return frames
    # (metadata-only device). Read one frame to confirm it's real.
    success, frame = capture.read()
    if not success or frame is None:
        capture.release()
        return None

    return capture


class CameraStream:
    """Opens the camera on demand and keeps only the newest frame, in a
    background thread. The device is only held open between start() and
    stop(), so the camera (and its LED) is truly off until requested."""

    def __init__(self, source):
        self.requested_source = source
        self.active_source = None
        self.error = None

        self.lock = threading.Lock()  # protects latest_frame / frame_id
        self.state_lock = threading.Lock()  # protects start/stop transitions
        self.latest_frame = None
        self.frame_id = 0  # increments on every new frame captured
        self.running = False
        self.thread = None
        self.backsub = None  # (re)created in start(), used for motion gating
        self.person_detected = False

    def start(self, timeout=5.0):
        """Open the camera if it isn't already running. Blocks until the
        camera is confirmed open (or failed) so the caller gets an accurate
        result. Returns True on success."""
        with self.state_lock:
            if self.running:
                return True

            self.error = None
            self.active_source = None
            self.latest_frame = None
            self.frame_id = 0
            self.running = True
            self.backsub = cv2.createBackgroundSubtractorMOG2(
                history=500, varThreshold=16, detectShadows=True
            )

            ready = threading.Event()
            self.thread = threading.Thread(
                target=self._run, args=(ready,), daemon=True
            )
            self.thread.start()
            ready.wait(timeout)

            if self.error or self.active_source is None:
                self.running = False
                self.thread.join(timeout=2)
                self.thread = None
                if self.error is None:
                    self.error = "Timed out opening the camera."
                return False
            return True

    def stop(self):
        """Stop capturing and release the device."""
        with self.state_lock:
            if not self.running:
                return
            self.running = False
            if self.thread:
                self.thread.join(timeout=2)
            self.thread = None
            self.active_source = None
            self.latest_frame = None
            self.frame_id = 0

    def _acquire(self):
        """Open the requested camera, or auto-detect a working one."""
        if self.requested_source is not None:
            capture = open_capture(self.requested_source)
            if capture is not None:
                self.active_source = self.requested_source
                return capture
            self.error = (
                f"Camera {self.requested_source!r} could not be opened or returns no frames. "
                "Set CAMERA_SOURCE to None to auto-detect, or run scan_cameras.py."
            )
            return None

        for index in range(MAX_INDEX_TO_PROBE):
            capture = open_capture(index)
            if capture is not None:
                self.active_source = index
                return capture

        self.error = "No working camera found. Check permissions or connections."
        return None

    def _run(self, ready):
        capture = self._acquire()
        if capture is None:
            ready.set()
            return
        ready.set()

        try:
            while self.running:
                success, frame = capture.read()
                if not success or frame is None:
                    time.sleep(0.01)
                    continue
                with self.lock:
                    self.latest_frame = frame
                    self.frame_id += 1
        finally:
            capture.release()

    def get_frame_if_newer(self, last_seen_id):
        """Return (frame, frame_id) only if a new frame arrived; else (None, last_seen_id)."""
        with self.lock:
            if self.latest_frame is None or self.frame_id == last_seen_id:
                return None, last_seen_id
            return self.latest_frame.copy(), self.frame_id


camera_stream = CameraStream(CAMERA_SOURCE)
encode_params = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]


def annotate_with_yolo(frame):
    """Draw person bounding boxes on the frame, but only run YOLO when
    background subtraction shows enough of the frame is moving."""
    mask = camera_stream.backsub.apply(frame)
    _, mask_clean = cv2.threshold(mask, 250, 255, cv2.THRESH_BINARY)
    total_pixels = mask_clean.shape[0] * mask_clean.shape[1]
    motion_ratio = cv2.countNonZero(mask_clean) / total_pixels

    if motion_ratio <= MOTION_THRESHOLD:
        camera_stream.person_detected = False
        return frame

    result = YOLO_MODEL(frame, classes=[0], verbose=False)[0]
    found = result.boxes is not None and len(result.boxes) > 0

    with camera_stream.lock:
        camera_stream.person_detected = found
    if found:
        return result.plot()
    return frame


def generate_frames():
    last_seen_id = 0
    min_interval = 1.0 / MAX_FPS

    while True:
        if not camera_stream.running or camera_stream.error:
            return  # camera off or failed; /status explains why on the page

        frame, last_seen_id = camera_stream.get_frame_if_newer(last_seen_id)
        if frame is None:
            time.sleep(0.005)  # no new frame yet, wait briefly
            continue

        frame = annotate_with_yolo(frame)

        ok, buffer = cv2.imencode(".jpg", frame, encode_params)
        if not ok:
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
        )

        time.sleep(min_interval)


@app.route("/status")
def status():
    if not camera_stream.running:
        return jsonify(ok=False, error="Camera is off", state="off")
    if camera_stream.error:
        return jsonify(ok=False, error=camera_stream.error, state="error")
    if camera_stream.active_source is None:
        return jsonify(ok=False, error="Opening camera...", state="starting")
    return jsonify(ok=True, source=str(camera_stream.active_source), state="on", person_detected=camera_stream.person_detected)


@app.route("/video_feed")
def video_feed():
    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )

@app.route('/api/camera/on', methods=['POST'])
def camera_on():
    if camera_stream.start():
        return jsonify({"status": "on", "source": str(camera_stream.active_source)}), 200
    return jsonify({"status": "error", "error": camera_stream.error}), 500

@app.route('/api/camera/off', methods=['POST'])
def camera_off():
    camera_stream.stop()
    return jsonify({"status": "off"}), 200

if __name__ == "__main__":
    try:
        # threaded=True so the page load and the long-lived stream request
        # are served concurrently. debug=False avoids the reloader opening
        # the camera twice.
        app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
    finally:
        camera_stream.stop()