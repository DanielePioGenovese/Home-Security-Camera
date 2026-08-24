# /// script
# dependencies = [
#   "opencv-python",
# ]
# ///
"""
Scan for available cameras and print which indices actually work.

On Linux (V4L2), a single physical webcam often exposes MULTIPLE /dev/videoN
nodes (e.g. /dev/video0 for the real video stream, /dev/video1 for metadata
only). That's why "the second camera" isn't always index 1 - run this first
to see which indices give you an actual frame.

Run with uv:
    uv run scan_cameras.py
"""

import cv2

MAX_INDEX_TO_TEST = 10

print(f"Scanning camera indices 0..{MAX_INDEX_TO_TEST - 1}...\n")

working_indices = []

for index in range(MAX_INDEX_TO_TEST):
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        cap.release()
        continue

    success, frame = cap.read()
    if success and frame is not None:
        height, width = frame.shape[:2]
        print(f"  index {index}: OK - frame size {width}x{height}")
        working_indices.append(index)
    else:
        print(f"  index {index}: opens but returns no frame (likely a metadata-only node, skip it)")

    cap.release()

print("\nWorking camera indices:", working_indices or "none found")
if working_indices:
    print("Use one of these values for CAMERA_INDEX in webcam_stream.py")