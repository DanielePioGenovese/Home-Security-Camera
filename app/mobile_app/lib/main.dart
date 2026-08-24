import 'dart:async';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

void main() {
  runApp(const MyApp());
}

class ApiService {
  final String baseUrl = "http://192.168.178.69:5000";

  static const _timeout = Duration(seconds: 10);

  Future<void> turnOnCamera() async {
    final response = await http
        .post(Uri.parse('$baseUrl/api/camera/on'))
        .timeout(_timeout);
    if (response.statusCode != 200) {
      throw Exception('Error loading camera');
    }
  }

  Future<void> turnOffCamera() async {
    final response = await http
        .post(Uri.parse('$baseUrl/api/camera/off'))
        .timeout(_timeout);
    if (response.statusCode != 200) {
      throw Exception('Errore nello spegnimento della camera');
    }
  }
}

/// Displays a `multipart/x-mixed-replace` MJPEG stream by scanning the raw
/// byte stream for JPEG start/end markers (FFD8...FFD9), since Flutter's
/// Image widget has no built-in support for this content type.
class MjpegView extends StatefulWidget {
  const MjpegView({super.key, required this.streamUrl});

  final String streamUrl;

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
      final request = http.Request('GET', Uri.parse(widget.streamUrl));
      final response = await _client!.send(request);
      if (response.statusCode != 200) {
        throw Exception('Stream non disponibile (${response.statusCode})');
      }
      _subscription = response.stream.listen(
        _onChunk,
        onError: (Object e) => _setError(e.toString()),
        onDone: () => _setError('Connessione al video terminata'),
        cancelOnError: true,
      );
    } catch (e) {
      _setError(e.toString());
    }
  }

  void _onChunk(List<int> chunk) {
    _buffer.addAll(chunk);

    while (true) {
      final start = _indexOfMarker(_buffer, _soi, 0);
      if (start == -1) {
        // No frame start found yet; avoid unbounded growth on junk data.
        if (_buffer.length > 2000000) _buffer.clear();
        break;
      }
      final end = _indexOfMarker(_buffer, _eoi, start + 2);
      if (end == -1) break;

      final frame = Uint8List.fromList(_buffer.sublist(start, end + 2));
      _buffer.removeRange(0, end + 2);

      if (mounted) {
        setState(() {
          _currentFrame = frame;
          _error = null;
        });
      }
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
  final ApiService _apiService = ApiService();

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
      setState(() => _isLoading = true);
      try {
        await _apiService.turnOffCamera();
        setState(() => _isOn = false);
      } catch (e) {
        _showError(e.toString());
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
            AspectRatio(
              aspectRatio: 4 / 3, // deve combaciare con FRAME_WIDTH/FRAME_HEIGHT in camera.py
              child: Container(
                decoration: BoxDecoration(
                  color: const Color.fromARGB(255, 0, 0, 0),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: Colors.deepPurple, width: 2),
                ),
                clipBehavior: Clip.antiAlias,
                child: _isOn
                    ? MjpegView(streamUrl: '${_apiService.baseUrl}/video_feed')
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
            if (_isOn)
              Image.asset(
                'assets/icon/icon.png',
                width: 300.0,
                height: 300.0,
              ),
          ],
        ),
      ),
    );
  }
}
