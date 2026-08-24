import cv2
from flask import Flask, Response, render_template_string
from ultralytics import YOLO

# URL of the MJPEG stream served by webcam_stream.py
SERVER_URL = "http://127.0.0.1:5000/video_feed"

app = Flask(__name__)

# Load the YOLO model used for person detection
MODEL = YOLO("yolo26n.pt")

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
        #status {
            color: #f5a;
            margin-top: 16px;
            min-height: 1.2em;
        }
    </style>
</head>
<body>
    <h1>Webcam Stream</h1>

    <img src="{{ url_for('video_yolo') }}" alt="camera stream">

    <div id="status">Connecting to camera...</div>

    <script>
        // Poll the status endpoint so that camera failures
        // are displayed on the page instead of showing
        // only a broken image.
        async function checkStatus() {
            try {
                const res = await fetch('/status');
                const data = await res.json();

                document.getElementById('status').textContent =
                    data.ok
                        ? 'Live - camera: ' + data.source
                        : 'ERROR: ' + data.error;

            } catch (e) {
                document.getElementById('status').textContent =
                    'Cannot reach server';
            }
        }

        // Check the camera status immediately when the page loads.
        checkStatus();

        // Continue checking the status every three seconds.
        setInterval(checkStatus, 3000);
    </script>
</body>
</html>
"""

# Create a background subtractor based on the MOG2 algorithm.
#
# The algorithm learns a statistical model of the background over time
# using the previous `history` frames. Pixels that significantly differ
# from the learned background are classified as foreground.
#
# detectShadows=True allows the algorithm to identify shadows separately.
# Shadows are represented by gray pixels (value 127), while foreground
# pixels are represented by white pixels (value 255).
backsub = cv2.createBackgroundSubtractorMOG2(
    history=500,
    varThreshold=16,
    detectShadows=True
)

# Open the MJPEG stream as if it were a local camera.
cap = cv2.VideoCapture(SERVER_URL)


@app.route("/")
def index():
    # Render the HTML page containing the live video stream.
    return render_template_string(PAGE_HTML)


def gen_frames():
    # Continuously read frames from the camera stream.
    while True:
        ok, frame = cap.read()

        # Skip the current iteration if the frame could not be read.
        if not ok:
            continue

        # Apply background subtraction to detect moving regions.
        mask = backsub.apply(frame)

        # Keep only pixels classified as real foreground.
        # Shadows are removed because they have a value of 127.
        _, mask_clean = cv2.threshold(
            mask,
            250,
            255,
            cv2.THRESH_BINARY
        )

        # Count the number of pixels classified as foreground.
        motion_pixels = cv2.countNonZero(mask_clean)

        # Calculate the total number of pixels in the frame.
        total_pixels = mask_clean.shape[0] * mask_clean.shape[1]

        # Calculate the percentage of the frame occupied by motion.
        motion_ratio = motion_pixels / total_pixels

        # By default, return the original frame without annotations.
        annotated = frame

        # Run YOLO only when enough motion is detected.
        # This avoids running object detection on every frame.
        if motion_ratio > 0.01:

            # Run YOLO and restrict detection to class 0 (person).
            results = MODEL(frame, classes=[0])

            # Get the results for the current frame.
            result = results[0]

            # Check whether YOLO detected at least one object.
            if result.boxes is not None and len(result.boxes) > 0:

                print("Person detected")

                # Draw the detected bounding boxes and labels on the frame.
                annotated = result.plot()

        # Encode the processed frame as JPEG.
        ok2, buffer = cv2.imencode(".jpg", annotated)

        # Skip the frame if JPEG encoding fails.
        if not ok2:
            continue

        # Convert the encoded image into raw bytes.
        frame_bytes = buffer.tobytes()

        # Yield the frame using the MJPEG format.
        # Each frame is sent as a separate JPEG image in the HTTP response.
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + frame_bytes
            + b"\r\n"
        )


@app.route("/video_yolo")
def video_yolo():
    # Stream the generated JPEG frames using the MJPEG protocol.
    return Response(
        gen_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


if __name__ == "__main__":
    # Start the Flask server on all network interfaces.
    # Port 5001 is used because the original camera stream
    # is already running on port 5000.
    app.run(
        host="0.0.0.0",
        port=5001,
        threaded=True
    )