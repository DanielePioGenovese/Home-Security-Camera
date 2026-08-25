# Home Security Camera

A self-hosted home surveillance system: a Python server runs person detection on a
webcam feed and pushes a notification to a Flutter app the moment someone walks in
front of the camera — even when the app is closed and you are not at home.

No cloud video storage, no third-party camera service, no open port on your router.
The video never leaves your own machine unless you ask for it.

```
┌──────────────┐   detects a person    ┌──────────────┐   push    ┌──────────┐
│  camera.py   │ ────────────────────▶ │   Firebase   │ ────────▶ │  phone   │
│  (your PC)   │                       │     (FCM)    │           │          │
│              │ ◀──── on/off, status, MJPEG stream ─────────────▶ │          │
└──────────────┘        (over Tailscale, never the open internet)  └──────────┘
```

## What it does

- **Detects people, not motion.** Background subtraction is only a cheap pre-filter;
  the actual decision is made by a YOLO model, so a curtain moving or a change in
  light does not wake you up at 3am.
- **Notifies you with the app closed.** Push delivery goes through Firebase Cloud
  Messaging, so it does not depend on the app running, or even on your phone being
  on your home network.
- **Streams live video on demand.** The phone can open an MJPEG stream to see what
  is happening, from anywhere.
- **Works away from home.** Reachability is provided by [Tailscale](https://tailscale.com),
  so the server is addressable from your phone over mobile data without exposing
  anything to the internet.

## Design notes

A few decisions in here are more interesting than the feature list:

**Detection lives in the capture thread, not in the HTTP stream.** The naive version
runs the detector inside the MJPEG generator, which means surveillance only happens
while somebody is watching the stream — useless. The camera thread is a *producer*
that detects unconditionally; the video stream is one *optional consumer*.

**The server is the single source of truth for state.** The app holds no persistent
state of its own: on startup it asks `/status` whether the camera is on. Kill the app,
reopen it, and the UI reflects reality instead of a stale local boolean.

**The FCM device token is persisted to disk.** It is only sent by the app at startup.
Held in memory alone, every server restart would silently disable notifications until
you happened to open the app again — the worst kind of failure for a security system,
because everything looks fine.

**YOLO is gated on motion.** Running inference on every frame of a live stream is far
too slow. Frames below `MOTION_THRESHOLD` skip the model entirely, so an empty room
costs almost nothing.

**Notifications fire on the rising edge.** A person standing in frame is one event, not
thirty per second.

## Security model

The threat this system actually faces is not a nation-state; it is *someone with your
Wi-Fi password*, and *anyone scanning the internet for open cameras*. Both are handled:

| Control | What it stops |
|---|---|
| **Bound to the Tailscale interface only** | The server is not listening on your LAN. A guest on your Wi-Fi opening `http://192.168.x.x:5000/video_feed` gets a refused connection — there is nothing on that address to talk to. |
| **No port forwarding, ever** | Nothing is exposed to the internet, so the scanners that index open camera streams cannot find it. |
| **WireGuard encryption (via Tailscale)** | Traffic between phone and server is already encrypted end-to-end. Adding HTTPS inside the tunnel would encrypt the same bytes twice. |
| **`X-API-Key` on every endpoint** | A device already inside the tailnet still cannot control the camera or open the stream. Compared with `hmac.compare_digest`, not `==`, so the comparison cannot leak the key through timing. |
| **Secrets never in git** | The Firebase service account and `google-services.json` are gitignored; the API key and server address are injected at build time, never hardcoded. |

The server **refuses to start** without `CAMERA_API_KEY`. A surveillance system that
quietly comes up with authentication disabled is worse than one that fails loudly.

**Known limit, stated honestly:** the API key ships inside the APK, so anyone holding
the APK can extract it. It defends the port against everything that does not have the
app — scripts, compromised IoT devices, future tailnet members. It is not a
cryptographic secret, and it is not pretending to be one.

## Requirements

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- Flutter 3.12+ (Android; iOS untested)
- A webcam
- A [Tailscale](https://tailscale.com) account, with the client installed on both the
  server machine and the phone
- A [Firebase](https://console.firebase.google.com) project with Cloud Messaging enabled

## Setup

### 1. Firebase

Create a project, then obtain two files — neither is in this repo, and neither should
ever be committed:

- **`app/camera/firebase-service-account.json`** — Project settings → Service accounts →
  *Generate new private key*. This grants admin access to your Firebase project.
- **`app/mobile_app/android/app/google-services.json`** — Project settings → Your apps →
  add an Android app with the applicationId from `android/app/build.gradle.kts`.

### 2. Generate an API key

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Keep it somewhere safe — the server and the app must both receive the same value.

### 3. Run the server

```bash
cd app/camera
CAMERA_API_KEY='your-key' uv run camera.py
```

It prints the address it bound to:

```
Listening on http://100.x.y.z:5000 (Tailscale only)
```

Set `CAMERA_SOURCE` in `camera.py` if it picks the wrong webcam (`None` auto-detects;
on Linux one physical camera often exposes several `/dev/videoN` nodes and some of them
never return a frame).

### 4. Build the app

Pass the same API key and the server address printed above:

```bash
cd app/mobile_app
flutter build apk --release \
  --dart-define=CAMERA_API_KEY='your-key' \
  --dart-define=CAMERA_BASE_URL='http://100.x.y.z:5000'
```

Install the APK from `build/app/outputs/flutter-apk/app-release.apk`, and make sure
Tailscale is connected on the phone.

If the two keys do not match, the app reports an explicit authentication error rather
than hanging.

## Configuration

| Where | Name | Meaning |
|---|---|---|
| env | `CAMERA_API_KEY` | Shared secret. **Required** — no default. |
| env | `CAMERA_HOST` | Override the bind address. Debug only; `0.0.0.0` re-exposes the LAN. |
| `--dart-define` | `CAMERA_API_KEY` | Must match the server. |
| `--dart-define` | `CAMERA_BASE_URL` | Server address, e.g. `http://100.x.y.z:5000`. |
| `camera.py` | `CAMERA_SOURCE` | Camera index, `/dev/videoN` path, or `None` to auto-detect. |
| `camera.py` | `MOTION_THRESHOLD` | Fraction of the frame that must move before YOLO runs. |
| `camera.py` | `FRAME_WIDTH` / `FRAME_HEIGHT` / `JPEG_QUALITY` / `MAX_FPS` | Stream quality vs. latency. |

## API

Every endpoint requires the `X-API-Key` header and returns `401` without it.

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/status` | Camera state: `on`, `off`, `starting`, `error`. |
| `GET` | `/video_feed` | `multipart/x-mixed-replace` MJPEG stream. |
| `POST` | `/api/camera/on` | Start capture. |
| `POST` | `/api/camera/off` | Stop capture and release the device. |
| `POST` | `/api/register_token` | Register the phone's FCM token. |

## Project layout

```
app/
├── camera/
│   ├── camera.py                    # capture thread, detection, Flask API, FCM
│   ├── firebase-service-account.json  (gitignored — you provide this)
│   └── device_token.json              (gitignored — created at runtime)
└── mobile_app/
    └── lib/main.dart                # ApiService, MJPEG decoder, UI
```

## Notes

- The MJPEG viewer is hand-written: Flutter's `Image` widget cannot consume
  `multipart/x-mixed-replace`, so the decoder scans the byte stream for JPEG
  `FFD8`/`FFD9` markers itself.
- Push notifications are sent with high priority, which is what lets them through
  Android's Doze mode when the phone has been idle. This is not optional — a
  normal-priority message can be held for hours, which for a security alert is the
  same as not sending it.
- iOS is untested. The FCM path should work; the notification setup will need APNs.

## License

Not yet licensed. Until a LICENSE file is added, default copyright applies and
the code is source-available rather than open source.
