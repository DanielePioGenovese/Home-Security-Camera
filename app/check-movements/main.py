import cv2
from flask import Flask, Response, render_template_string
from ultralytics import YOLO

# URL of the MJPEG stream served by webcam_stream.py
SERVER_URL = "http://127.0.0.1:5000/video_feed"
app = Flask(__name__)

MODEL = YOLO('yolo26n.pt')

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
    <img src="{{ url_for('video_yolo') }}" alt="camera stream">
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

# Background subtractor: learns a per-pixel statistical model of the empty
# scene over time (using the last `history` frames), then flags pixels that
# deviate from that model as foreground (motion).
#   - detectShadows=True makes it label shadows separately (gray, value 127)
#     instead of counting them as real motion (white, value 255).
backsub = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=16, detectShadows=True)

# Read the stream as if it were a local camera
cap = cv2.VideoCapture(SERVER_URL)

@app.route("/")
def index():
    return render_template_string(PAGE_HTML)

def gen_frames():
    while True:
        ok, frame = cap.read()
        if not ok:
            continue

        mask = backsub.apply(frame)
        _, mask_clean = cv2.threshold(mask, 250, 255, cv2.THRESH_BINARY)
        motion_pixels = cv2.countNonZero(mask_clean)
        total_pixels = mask_clean.shape[0] * mask_clean.shape[1]
        motion_ratio = motion_pixels / total_pixels

        annotated = frame
        if motion_ratio > 0.01:
            results = MODEL(frame, classes=[0])  # solo persone
            result = results[0]
            if result.boxes is not None and len(result.boxes) > 0:
                print("Persona rilevata")
                annotated = result.plot()  # disegna i box sul frame

        ok2, buffer = cv2.imencode('.jpg', annotated)
        if not ok2:
            continue
        frame_bytes = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

@app.route("/video_yolo")
def video_yolo():
    return Response(gen_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, threaded=True)