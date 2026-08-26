import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/material.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:http/http.dart' as http;

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await Firebase.initializeApp();
  runApp(const MyApp());
}

class ApiService {
  /// Address of the camera server. Injected at build time so no private
  /// network address is committed to source control:
  ///   `flutter build apk --dart-define=CAMERA_BASE_URL=http://HOST:5000`
  final String baseUrl = const String.fromEnvironment(
    'CAMERA_BASE_URL',
    defaultValue: 'http://127.0.0.1:5000',
  );

  static const _timeout = Duration(seconds: 10);

  /// Shared secret the server requires on every request. Injected at build
  /// time (`flutter build apk --dart-define=CAMERA_API_KEY=...`) so it is
  /// never committed to source control.
  static const _apiKey = String.fromEnvironment('CAMERA_API_KEY');

  Map<String, String> get authHeaders => {'X-API-Key': _apiKey};

  Future<void> turnOnCamera() async {
    final response = await http
        .post(Uri.parse('$baseUrl/api/camera/on'), headers: authHeaders)
        .timeout(_timeout);
    if (response.statusCode != 200) {
      throw Exception('Error loading camera');
    }
  }

  Future<void> turnOffCamera() async {
    final response = await http
        .post(Uri.parse('$baseUrl/api/camera/off'), headers: authHeaders)
        .timeout(_timeout);
    if (response.statusCode != 200) {
      throw Exception('Error turning off camera');
    }
  }

  Future<void> registerToken(String token) async {
    await http
        .post(
          Uri.parse('$baseUrl/api/register_token'),
          headers: {...authHeaders, 'Content-Type': 'application/json'},
          body: jsonEncode({'token': token}),
        )
        .timeout(_timeout);
  }

  Future<String> cameraStatus() async {

    final response = await http
        .get(Uri.parse('$baseUrl/status'), headers: authHeaders)
        .timeout(_timeout);

    if (response.statusCode == 401) {
      throw Exception('Rejected by the server: wrong or missing API key');
    }

    String camStatus = json.decode(response.body)['state'];

    return camStatus;

    }
}


/// Displays a `multipart/x-mixed-replace` MJPEG stream by scanning the raw
/// byte stream for JPEG start/end markers (FFD8...FFD9), since Flutter's
/// Image widget has no built-in support for this content type.
class MjpegView extends StatefulWidget {
  const MjpegView({
    super.key,
    required this.streamUrl,
    this.headers = const {},
  });

  final String streamUrl;
  final Map<String, String> headers;

  @override
  State<MjpegView> createState() => _MjpegViewState();
}

class _MjpegViewState extends State<MjpegView> {
  static const _soi = [0xFF, 0xD8]; // JPEG start of image
  static const _eoi = [0xFF, 0xD9]; // JPEG end of image

  http.Client? _client;
  StreamSubscription<List<int>>? _subscription;
  final List<int> _buffer = [];
  Uint8List? _currentFrame;
  String? _error;

  @override
  void initState() {
    super.initState();
    _connect();
  }

  @override
  void didUpdateWidget(covariant MjpegView oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.streamUrl != widget.streamUrl) {
      _disconnect();
      _connect();
    }
  }

  Future<void> _connect() async {
    _client = http.Client();
    try {
      final request = http.Request('GET', Uri.parse(widget.streamUrl))
        ..headers.addAll(widget.headers);
      final response = await _client!.send(request);
      if (response.statusCode != 200) {
        throw Exception('Stream unavailable (${response.statusCode})');
      }
      _subscription = response.stream.listen(
        _onChunk,
        onError: (Object e) => _setError(e.toString()),
        onDone: () => _setError('Video connection closed'),
        cancelOnError: true,
      );
    } catch (e) {
      _setError(e.toString());
    }
  }

  void _onChunk(List<int> chunk) {
    _buffer.addAll(chunk);

    // A single network chunk can contain several complete JPEG frames
    // (e.g. after a brief stall on the phone's side). Decoding and
    // rendering every one of them would make the UI fall further and
    // further behind live video, which is the exact lag this stream is
    // built to avoid server-side. So extract all complete frames but only
    // keep the newest: older ones in the same chunk are simply discarded.
    Uint8List? latestFrame;
    var consumed = 0;

    while (true) {
      final start = _indexOfMarker(_buffer, _soi, consumed);
      if (start == -1) {
        consumed = _buffer.length > 2 ? _buffer.length - 1 : consumed;
        break;
      }
      final end = _indexOfMarker(_buffer, _eoi, start + 2);
      if (end == -1) break;

      latestFrame = Uint8List.fromList(_buffer.sublist(start, end + 2));
      consumed = end + 2;
    }

    if (consumed > 0) {
      _buffer.removeRange(0, consumed);
      // Avoid unbounded growth on junk data with no markers at all.
      if (_buffer.length > 2000000) _buffer.clear();
    }

    if (latestFrame != null && mounted) {
      setState(() {
        _currentFrame = latestFrame;
        _error = null;
      });
    }
  }

  int _indexOfMarker(List<int> data, List<int> marker, int from) {
    for (var i = from; i <= data.length - marker.length; i++) {
      if (data[i] == marker[0] && data[i + 1] == marker[1]) return i;
    }
    return -1;
  }

  void _setError(String message) {
    if (mounted) setState(() => _error = message);
  }

  void _disconnect() {
    _subscription?.cancel();
    _subscription = null;
    _client?.close();
    _client = null;
    _buffer.clear();
  }

  @override
  void dispose() {
    _disconnect();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null) {
      return Center(
        child: Text(_error!, style: const TextStyle(color: Colors.redAccent)),
      );
    }
    if (_currentFrame == null) {
      return const Center(
        child: CircularProgressIndicator(color: Colors.white70),
      );
    }
    return Image.memory(
      _currentFrame!,
      gaplessPlayback: true,
      fit: BoxFit.contain,
    );
  }
}


class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Home Security Camera',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: const Color.fromARGB(255, 40, 40, 40)),
        useMaterial3: true,
      ),
      home: const CameraHomePage(title: 'Home Security Camera'),
    );
  }
}

class CameraHomePage extends StatefulWidget {
  const CameraHomePage({super.key, required this.title});

  final String title;

  @override
  State<CameraHomePage> createState() => _CameraHomePageState();
}


class _CameraHomePageState extends State<CameraHomePage> {
  bool _isOn = false;
  bool _isLoading = false;
  bool _personDetected = false;

  // Whether Android will actually display an alert. null until checked.
  // A surveillance app that cannot notify is broken, so this is surfaced in
  // the UI instead of being discovered the night it matters.
  bool? _notificationsEnabled;

  // Set when a show() call throws, e.g. a missing icon resource. Without
  // this the exception is swallowed by the unawaited call in onMessage.
  String? _notificationError;
  final ApiService _apiService = ApiService();
  final FlutterLocalNotificationsPlugin _notifications =
      FlutterLocalNotificationsPlugin();

  Timer? _detectionIndicatorTimer;

  @override
  void initState() {
    super.initState();
    _initNotifications();
    _initPushNotifications();
    _initCameraStatus();
  }

  Future<void> _initNotifications() async {
    await _notifications.initialize(
      settings: const InitializationSettings(
        android: AndroidInitializationSettings('ic_notification'),
      ),
    );
    // Android 13+ requires the permission at runtime, otherwise the
    // notification silently never shows up.
    final android = _notifications.resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin>();
    await android?.requestNotificationsPermission();

    // Create the channel now rather than letting the first show() create it.
    // Android drops any notification whose channel does not exist, and a
    // notification arriving while the app is killed is rendered by Play
    // Services - none of this code runs then, so the channel has to already
    // be there. Channel settings are immutable after creation: changing the
    // importance below has no effect until the app is reinstalled.
    await android?.createNotificationChannel(
      const AndroidNotificationChannel(
        'camera_alerts',
        'Camera alerts',
        description: 'Alerts when a person is detected by the camera',
        importance: Importance.high,
      ),
    );

    // The request result only describes this one prompt: it is null when no
    // prompt was shown, and stays true even if the user revokes the
    // permission later from settings. Ask the system for the current state
    // instead - that is the answer that matters.
    final enabled = await android?.areNotificationsEnabled();
    if (mounted) setState(() => _notificationsEnabled = enabled);
  }

  // Registers this phone with the server so it knows where to push
  // detections, and listens for pushes that arrive while the app is open.
  // Detection now flows server -> phone via FCM instead of the app polling
  // the server, so notifications keep working even when the app is
  // backgrounded or fully closed.
  Future<void> _initPushNotifications() async {
    final settings = await FirebaseMessaging.instance.requestPermission();
    if (settings.authorizationStatus != AuthorizationStatus.authorized &&
        mounted) {
      setState(() => _notificationsEnabled = false);
    }

    final token = await FirebaseMessaging.instance.getToken();
    if (token != null) {
      await _apiService.registerToken(token);
    }
    // The token can change (e.g. app reinstall); keep the server in sync.
    FirebaseMessaging.instance.onTokenRefresh.listen(_apiService.registerToken);

    // Android does not show FCM notifications on its own while the app is
    // in the foreground, so this shows one manually in that case; when the
    // app is backgrounded or closed, Google Play Services displays it
    // without any of this code running.
    FirebaseMessaging.onMessage.listen((message) {
      _showPersonNotification();
      _flashDetectionIndicator();
    });
  }

  Future<void> _initCameraStatus() async {
    String cameraStatus = await _apiService.cameraStatus();

    if (cameraStatus == 'on'){
      setState(() => _isOn = true);
    }
  }

  void _flashDetectionIndicator() {
    if (!mounted) return;
    setState(() => _personDetected = true);
    _detectionIndicatorTimer?.cancel();
    _detectionIndicatorTimer = Timer(const Duration(seconds: 5), () {
      if (mounted) setState(() => _personDetected = false);
    });
  }

  Future<void> _showPersonNotification() async {
    try {
      await _notifications.show(
        id: 0,
        title: 'Motion detected',
        body: 'A person was detected in front of the camera',
        notificationDetails: const NotificationDetails(
          android: AndroidNotificationDetails(
            'camera_alerts',
            'Camera alerts',
            icon: 'ic_notification',
            priority: Priority.high,
            importance: Importance.high,
          ),
        ),
      );
    } catch (e) {
      // This runs from an unawaited call in the onMessage listener, so an
      // uncaught error here would vanish without a trace.
      if (mounted) setState(() => _notificationError = e.toString());
    }
  }

  Future<void> _turnOn() async {
      setState(() => _isLoading = true);
      try {
        await _apiService.turnOnCamera();
        setState(() => _isOn = true);
      } catch (e) {
        _showError(e.toString());
      } finally {
        setState(() => _isLoading = false);
      }
    }

  Future<void> _turnOff() async {
      // Unmount MjpegView (and close its stream) before the network call,
      // not after: the server closes /video_feed as soon as it receives
      // this request, and if our stream is still actively being read at
      // that point, dart:io reports the resulting closed connection as a
      // ClientException instead of a normal end-of-stream. Disconnecting
      // client-side first avoids that race entirely.
      setState(() {
        _isLoading = true;
        _isOn = false;
      });
      try {
        await _apiService.turnOffCamera();
      } catch (e) {
        _showError(e.toString());
        setState(() => _isOn = true);
      } finally {
        setState(() => _isLoading = false);
      }
    }

  void _showError(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message), backgroundColor: Colors.red),
    );
  }

  @override
  void dispose() {
    _detectionIndicatorTimer?.cancel();
    super.dispose();
  }

  /// Persistent banner shown when alerts cannot reach the user. Deliberately
  /// not a SnackBar: this condition does not go away on its own, and a
  /// warning that disappears after four seconds is a warning nobody reads.
  Widget _buildNotificationWarning() {
    final message = _notificationError ??
        'Notifications are disabled. You will NOT be alerted when someone '
            'is detected. Enable them in Settings > Apps > this app > '
            'Notifications.';
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 16),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: const Color(0xFF7F1D1D),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.notifications_off, color: Colors.white, size: 20),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              message,
              style: const TextStyle(color: Colors.white, fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        backgroundColor: Theme.of(context).colorScheme.inversePrimary,
        title: Text(widget.title),
      ),
      body: Padding(
        padding: const EdgeInsets.all(16.0),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.start,
          children: [
            if (_notificationsEnabled == false || _notificationError != null)
              _buildNotificationWarning(),
            AspectRatio(
              aspectRatio: 4 / 3, // must match FRAME_WIDTH/FRAME_HEIGHT in camera.py
              child: Container(
                decoration: BoxDecoration(
                  color: const Color.fromARGB(255, 0, 0, 0),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: Colors.deepPurple, width: 2),
                ),
                clipBehavior: Clip.antiAlias,
                child: _isOn
                    ? MjpegView(
                        streamUrl: '${_apiService.baseUrl}/video_feed',
                        headers: _apiService.authHeaders,
                      )
                    : const Center(
                        child: Text(
                          'Streaming off',
                          style: TextStyle(color: Colors.white70, fontSize: 16),
                        ),
                      ),
              ),
            ),
            const SizedBox(height: 24),
            Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                ElevatedButton(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: Colors.green,
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(horizontal: 32, vertical: 16),
                  ),
                  onPressed: (_isOn || _isLoading) ? null : _turnOn,
                  child: _isLoading && !_isOn
                      ? const SizedBox(
                          width: 16, height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                        )
                      : const Text('ON'),
                ),
                const SizedBox(width: 24),
                ElevatedButton(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color.fromARGB(255, 239, 28, 12),
                    foregroundColor: Colors.white,
                    padding: const EdgeInsets.symmetric(horizontal: 32, vertical: 16),
                  ),
                  onPressed: _isOn ? _turnOff : null,
                  child: const Text('OFF'),
                ),
              ],
            ),
            if (_personDetected)
              Padding(
                padding: const EdgeInsets.only(top: 16.0),
                child: Image.asset(
                  'assets/icon/icon.png',
                  width: 80.0,
                  height: 80.0,
                ),
              ),
          ],
        ),
      ),
    );
  }
}
