# /// script
# dependencies = [
#   "flask",
#   "opencv-python",
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
from flask import Flask, Response, jsonify, render_template_string

app = Flask(__name__)

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

PAGE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>Webcam Live Stream</title>
    <style>
        body {
            background: #111;
            color: #eee;
            font-family: sans-serif;
            text-align: center;
            padding-top: 40px;
        }
        img {
            border: 4px solid #333;
            border-radius: 8px;
            max-width: 90%;
        }
        #status { color: #f5a; margin-top: 16px; min-height: 1.2em; }
    </style>
</head>
<body>
    <h1>Webcam Stream</h1>
    <img src="{{ url_for('video_feed') }}" alt="camera stream">
    <div id="status">Connecting to camera...</div>
    <script>
        // Poll the status endpoint so a camera failure shows up on the page
        // instead of just leaving a broken image with no explanation.
        async function checkStatus() {
            try {
                const res = await fetch('/status');
                const data = await res.json();
                document.getElementById('status').textContent =
                    data.ok ? 'Live - camera: ' + data.source : 'ERROR: ' + data.error;
            } catch (e) {
                document.getElementById('status').textContent = 'Cannot reach server';
            }
        }
        checkStatus();
        setInterval(checkStatus, 3000);
    </script>
</body>
</html>
"""


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
    """Opens the camera and keeps only the newest frame, in a background thread."""

    def __init__(self, source):
        self.requested_source = source
        self.active_source = None
        self.error = None

        self.lock = threading.Lock()
        self.latest_frame = None
        self.frame_id = 0  # increments on every new frame captured
        self.running = True

        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

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

    def _run(self):
        capture = self._acquire()
        if capture is None:
            return

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

    def stop(self):
        self.running = False
        self.thread.join(timeout=2)


camera_stream = CameraStream(CAMERA_SOURCE)
encode_params = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]


def generate_frames():
    last_seen_id = 0
    min_interval = 1.0 / MAX_FPS

    while True:
        if camera_stream.error:
            return  # camera failed; /status explains why on the page

        frame, last_seen_id = camera_stream.get_frame_if_newer(last_seen_id)
        if frame is None:
            time.sleep(0.005)  # no new frame yet, wait briefly
            continue

        ok, buffer = cv2.imencode(".jpg", frame, encode_params)
        if not ok:
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n"
        )

        time.sleep(min_interval)


@app.route("/")
def index():
    return render_template_string(PAGE_HTML)


@app.route("/status")
def status():
    if camera_stream.error:
        return jsonify(ok=False, error=camera_stream.error)
    if camera_stream.active_source is None:
        return jsonify(ok=False, error="Opening camera...")
    return jsonify(ok=True, source=str(camera_stream.active_source))


@app.route("/video_feed")
def video_feed():
    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


if __name__ == "__main__":
    try:
        # threaded=True so the page load and the long-lived stream request
        # are served concurrently. debug=False avoids the reloader opening
        # the camera twice.
        app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
    finally:
        camera_stream.stop()