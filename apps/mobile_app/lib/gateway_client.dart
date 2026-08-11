/// Gateway HTTP client — the ONLY backend the phone talks to.
///
/// Device actions (tasks, approvals, stop) require the device JWT acquired
/// through pairing; the frontend never calls the task-runner directly.
library;

import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import 'models.dart';

class GatewayException implements Exception {
  const GatewayException(this.message, {this.statusCode = 0});

  final String message;
  final int statusCode;

  @override
  String toString() => message;
}

class GatewayClient {
  GatewayClient({required this.baseUrl, http.Client? client})
      : _client = client ?? http.Client();

  final String baseUrl;
  final http.Client _client;

  Uri _uri(String path) => Uri.parse('$baseUrl$path');

  Map<String, String> _headers({String? jwt}) => {
        'Content-Type': 'application/json',
        if (jwt != null) 'Authorization': 'Bearer $jwt',
      };

  /// Step 1 of pairing: ask the gateway for a scannable token.
  Future<Map<String, dynamic>> startPairing(String deviceName) async {
    final resp = await _client.post(
      _uri('/auth/device/pair'),
      headers: _headers(),
      body: jsonEncode({'name': deviceName}),
    );
    return _decode(resp);
  }

  /// Step 2: swap the scanned token for a device JWT.
  Future<DeviceSession> exchangePairing(String deviceId, String token) async {
    final resp = await _client.post(
      _uri('/auth/device/exchange'),
      headers: _headers(),
      body: jsonEncode({'device_id': deviceId, 'token': token}),
    );
    return DeviceSession.fromJson(_decode(resp));
  }

  /// Live status + toggle: the phone can force-refresh the queue.
  Future<List<Approval>> listApprovals(String jwt) async {
    final resp = await _client.get(
      _uri('/v1/approvals'),
      headers: _headers(jwt: jwt),
    );
    final body = _decode(resp);
    return ((body['approvals'] as List<dynamic>?) ?? [])
        .map((a) => Approval.fromJson(a as Map<String, dynamic>))
        .toList();
  }

  Future<List<Task>> listTasks(String jwt) async {
    final resp = await _client.get(_uri('/v1/tasks'), headers: _headers(jwt: jwt));
    final body = _decode(resp);
    return ((body['tasks'] as List<dynamic>?) ?? [])
        .map((t) => Task.fromJson(t as Map<String, dynamic>))
        .toList();
  }

  Future<List<ActivityEvent>> listActivity(String jwt) async {
    final resp =
        await _client.get(_uri('/v1/activity'), headers: _headers(jwt: jwt));
    final body = _decode(resp);
    /// The gateway wraps audit events as: {"events": [...]}.
    return ((body['events'] as List<dynamic>?) ?? [])
        .map((e) => ActivityEvent.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<Map<String, dynamic>> approve(String jwt, String approvalId) async {
    final resp = await _client.post(
      _uri('/v1/approvals/$approvalId/approve'),
      headers: _headers(jwt: jwt),
    );
    return _decode(resp);
  }

  Future<Map<String, dynamic>> reject(String jwt, String approvalId) async {
    final resp = await _client.post(
      _uri('/v1/approvals/$approvalId/reject'),
      headers: _headers(jwt: jwt),
    );
    return _decode(resp);
  }

  /// Stop Everything — emergency halt of every task at the gateway.
  Future<Map<String, dynamic>> stopEverything(String jwt) async {
    final resp =
        await _client.post(_uri('/v1/stop'), headers: _headers(jwt: jwt));
    return _decode(resp);
  }

  /// Start a remote-control session task. It pauses at the approval gate, so
  /// the phone must approve the diff card before any input path opens.
  Future<Map<String, dynamic>> startRemoteSession(String jwt, {int timeoutMinutes = 5}) async {
    final resp = await _client.post(
      _uri('/v1/remote/start'),
      headers: _headers(jwt: jwt),
      body: jsonEncode({'name': 'phone', 'timeout_minutes': timeoutMinutes}),
    );
    return _decode(resp);
  }

  /// The current live session for this device, or `null`.
  Future<RemoteSession?> activeRemoteSession(String jwt) async {
    final resp = await _client.get(
      _uri('/v1/remote/session'),
      headers: _headers(jwt: jwt),
    );
    final body = _decode(resp);
    final s = body['session'];
    if (s == null || s is! Map<String, dynamic>) return null;
    return RemoteSession.fromJson(s);
  }

  /// One on-demand screen frame (PNG bytes). Throttled by the gateway: 2s
  /// minimum between frames — mirroring is a peek, never a stream.
  Future<Uint8List> remoteFrame(String jwt) async {
    final resp = await _client.get(
      _uri('/v1/remote/screenshot'),
      headers: _headers(jwt: jwt),
    );
    if (resp.statusCode < 200 || resp.statusCode >= 300) {
      throw GatewayException(
        _decode(resp)['detail'] as String? ?? 'HTTP ${resp.statusCode}',
        statusCode: resp.statusCode,
      );
    }
    return resp.bodyBytes;
  }

  /// Send one keyboard/mouse action inside a live session.
  Future<Map<String, dynamic>> remoteInput(
    String jwt,
    String sessionId, {
    required String action,
    String? text,
    int? x,
    int? y,
  }) async {
    final resp = await _client.post(
      _uri('/v1/remote/input'),
      headers: _headers(jwt: jwt),
      body: jsonEncode({
        'session_id': sessionId,
        'action': action,
        'text': ?text,
        'x': ?x,
        'y': ?y,
      }),
    );
    return _decode(resp);
  }

  /// Explicitly end this device's remote session now (before auto-expiry).
  Future<Map<String, dynamic>> endRemoteSession(String jwt) async {
    final resp = await _client.post(
      _uri('/v1/remote/end'),
      headers: _headers(jwt: jwt),
    );
    return _decode(resp);
  }

  /// Lock the laptop immediately. Execute-tier and session-gated: requires a
  /// live session, then locks and ends the session in one action.
  Future<Map<String, dynamic>> lockWorkstation(String jwt) async {
    final resp = await _client.post(
      _uri('/v1/remote/lock'),
      headers: _headers(jwt: jwt),
    );
    return _decode(resp);
  }

  Map<String, dynamic> _decode(http.Response resp) {
    Map<String, dynamic>? body;
    try {
      body = (jsonDecode(resp.body) as Map<String, dynamic>?);
    } catch (_) {
      body = null;
    }
    if (resp.statusCode < 200 || resp.statusCode >= 300) {
      final detail = body?['detail'];
      throw GatewayException(
        detail is String ? detail : 'HTTP ${resp.statusCode}',
        statusCode: resp.statusCode,
      );
    }
    return body ?? {};
  }
}

/// Builds a GatewayClient pointed at the paired laptop (Tailscale IP falls
/// back to the simulator host loopback for local dev).
GatewayClient defaultGatewayClient({String? overrideBase}) {
  final origin = overrideBase ?? const String.fromEnvironment('VIORIS_GATEWAY');
  final base = origin.isNotEmpty
      ? origin
      : (Platform.isAndroid
          ? 'http://10.0.2.2:8420' // Android emulator -> host loopback
          : 'http://127.0.0.1:8420');
  return GatewayClient(baseUrl: base);
}