/// Shared app state: pairing session, live queue, socket status.
///
/// Everything a screen shows is served from here; the backend is only touched
/// through `GatewayClient` (device JWT) and `GatewaySocket`.
library;

import 'package:flutter/foundation.dart';

import 'gateway_client.dart';
import 'gateway_socket.dart';
import 'models.dart';

enum PairingPhase { enterToken, paired, failed }

class AppState extends ChangeNotifier {
  AppState(this._client);

  final GatewayClient _client;

  PairingPhase phase = PairingPhase.enterToken;
  String? error;
  String? deviceName;
  DeviceSession? session;
  List<Approval> approvals = [];
  List<Task> tasks = [];
  List<ActivityEvent> activity = [];
  SocketStatus socketStatus = SocketStatus.connecting;
  bool stopInFlight = false;
  RemoteSession? remoteSession;
  Uint8List? remoteFrame;
  bool remoteRefreshing = false;

  GatewaySocket? _socket;

  GatewayClient get client => _client;

  bool get paired => session != null;

  /// Try to exchange a scanned pairing token for a device session.
  Future<void> pairWithToken(String deviceId, String token) async {
    error = null;
    notifyListeners();
    try {
      session = await _client.exchangePairing(deviceId, token);
      phase = PairingPhase.paired;
      _startSocket();
      await refreshAll();
    } on GatewayException catch (e) {
      error = e.message;
      phase = PairingPhase.failed;
    } catch (e) {
      error = e.toString();
      phase = PairingPhase.failed;
    }
    notifyListeners();
  }

  void _startSocket() {
    final jwt = session!.jwt;
    final base = _client.baseUrl.replaceAll('http', 'ws');
    _socket?.dispose();
    _socket = GatewaySocket(baseWsUrl: base, jwt: jwt);
    _socket!.state.listen(_onSocketState);
    _socket!.events.listen(_onSocketEvent);
    _socket!.connect();
  }

  void _onSocketState(SocketStatus status) {
    socketStatus = status;
    notifyListeners();
  }

  void _onSocketEvent(Map<String, dynamic> event) {
    // A live stop or approval decision should refresh the queue.
    if (event['type'] == 'pending_approval' ||
        event['type'] == 'approved' ||
        event['type'] == 'rejected' ||
        event['type'] == 'stopped_all') {
      refreshAll();
    }
  }

  Future<void> refreshAll() async {
    final jwt = session?.jwt;
    if (jwt == null) return;
    try {
      approvals = await _client.listApprovals(jwt);
      tasks = await _client.listTasks(jwt);
      activity = await _client.listActivity(jwt);
    } on GatewayException catch (e) {
      error = e.message;
    } catch (e) {
      error = e.toString();
    }
    notifyListeners();
  }

  Future<void> decide(Approval approval, bool approve) async {
    final jwt = session?.jwt;
    if (jwt == null) return;
    try {
      if (approve) {
        await _client.approve(jwt, approval.approvalId);
      } else {
        await _client.reject(jwt, approval.approvalId);
      }
      await refreshAll();
    } on GatewayException catch (e) {
      error = e.message;
      notifyListeners();
    }
  }

  /// Stop Everything — halts every task at the gateway.
  Future<void> stopEverything() async {
    final jwt = session?.jwt;
    if (jwt == null || stopInFlight) return;
    stopInFlight = true;
    notifyListeners();
    try {
      await _client.stopEverything(jwt);
      remoteSession = null; // stopping also kills this device's remote session
      remoteFrame = null;
      await refreshAll();
    } on GatewayException catch (e) {
      error = e.message;
    } finally {
      stopInFlight = false;
      notifyListeners();
    }
  }

  // ── Remote control (Phase 5.3) ──────────────────────────────────────────

  /// Ask to control the laptop. Creates an execute-tier task that pauses at
  /// the approval gate — the phone must Approve before a session can start.
  Future<bool> requestRemoteControl({int timeoutMinutes = 5}) async {
    final jwt = session?.jwt;
    if (jwt == null) return false;
    try {
      await _client.startRemoteSession(jwt, timeoutMinutes: timeoutMinutes);
      await refreshAll();
      return true;
    } on GatewayException catch (e) {
      error = e.message;
      notifyListeners();
      return false;
    }
  }

  /// Refresh the active session + pull one on-demand frame (throttled server-side).
  Future<void> refreshRemote() async {
    final jwt = session?.jwt;
    if (jwt == null || remoteRefreshing) return;
    remoteRefreshing = true;
    notifyListeners();
    try {
      remoteSession = await _client.activeRemoteSession(jwt);
      if (remoteSession != null && !remoteSession!.expired) {
        remoteFrame = await _client.remoteFrame(jwt);
      } else {
        remoteFrame = null;
      }
    } on GatewayException catch (e) {
      error = e.message;
    } finally {
      remoteRefreshing = false;
      notifyListeners();
    }
  }

  Future<void> sendRemoteAction(String action, {String? text, int? x, int? y}) async {
    final jwt = session?.jwt;
    final sess = remoteSession;
    if (jwt == null || sess == null || sess.expired) {
      error = 'no active remote session';
      notifyListeners();
      return;
    }
    try {
      await _client.remoteInput(
        jwt,
        sess.sessionId,
        action: action,
        text: text,
        x: x,
        y: y,
      );
      remoteFrame = await _client.remoteFrame(jwt); // verify the effect
    } on GatewayException catch (e) {
      error = e.message;
    }
    notifyListeners();
  }

  Future<void> endRemoteSession() async {
    final jwt = session?.jwt;
    if (jwt == null) return;
    try {
      await _client.endRemoteSession(jwt);
      remoteSession = null;
      remoteFrame = null;
    } on GatewayException catch (e) {
      error = e.message;
    }
    notifyListeners();
  }

  /// Lock the laptop now (execute-tier, session-gated). After locking, this
  /// device has no live session, so the phone loses control immediately.
  Future<void> lockWorkstation() async {
    final jwt = session?.jwt;
    if (jwt == null) return;
    try {
      await _client.lockWorkstation(jwt);
      remoteSession = null;
      remoteFrame = null;
    } on GatewayException catch (e) {
      error = e.message;
    }
    notifyListeners();
  }

  @override
  void dispose() {
    _socket?.dispose();
    super.dispose();
  }
}