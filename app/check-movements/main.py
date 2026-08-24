import cv2
from flask import Flask

# URL of the MJPEG stream served by webcam_stream.py
SERVER_URL = "http://127.0.0.1:5000/video_feed"
app = Flask(__name__)

# Background subtractor: learns a per-pixel statistical model of the empty
# scene over time (using the last `history` frames), then flags pixels that
# deviate from that model as foreground (motion).
#   - detectShadows=True makes it label shadows separately (gray, value 127)
#     instead of counting them as real motion (white, value 255).
backsub = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=16, detectShadows=True)

# Read the stream as if it were a local camera
cap = cv2.VideoCapture(SERVER_URL)

while True:
    ok, frame = cap.read()

    if ok:
        # Compare this frame against the learned background model.
        # Output is a grayscale mask, same size as the frame:
        #   0   = background (no change)
        #   127 = shadow (change, but shadow-like -> not real motion)
        #   255 = foreground (real motion)
        mask = backsub.apply(frame)

        # Drop shadows: keep only pixels that are (almost) pure white (255),
        # turning shadows (127) and background (0) both into black (0).   
        # Result is a clean binary mask: white = motion, black = everything else.
        _, mask_clean = cv2.threshold(mask, 250, 255, cv2.THRESH_BINARY)

        # Count how many pixels in the mask are flagged as motion
        motion_pixels = cv2.countNonZero(mask_clean)

        # Express motion as a ratio of the frame size, not a raw pixel count,
        # so the threshold below doesn't depend on the camera resolution
        total_pixels = mask_clean.shape[0] * mask_clean.shape[1]
        motion_ratio = motion_pixels / total_pixels

        # Trigger when more than 10% of the frame changed.
        # NOTE: tune this threshold empirically for your camera/scene.
        if motion_ratio > 0.1:
            print("MOVEMENT")