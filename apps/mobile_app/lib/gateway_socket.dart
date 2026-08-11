/// WebSocket channel to the gateway over the paired device JWT (Prompt 5.2).
///
/// The phone subscribes once and receives live approval/status/stopped
/// events. Reconnects with backoff when the socket drops; a revoked device
/// gets a 4403 and stays disconnected.
library;

import 'dart:async';
import 'dart:convert';

import 'package:web_socket_channel/web_socket_channel.dart';

enum SocketStatus { connecting, connected, closed, revoked }

class GatewaySocket {
  GatewaySocket({required this.baseWsUrl, required this.jwt});

  final String baseWsUrl;
  final String jwt;

  final _stateCtrl = StreamController<SocketStatus>.broadcast();
  final _eventCtrl = StreamController<Map<String, dynamic>>.broadcast();

  Stream<SocketStatus> get state => _stateCtrl.stream;
  Stream<Map<String, dynamic>> get events => _eventCtrl.stream;

  WebSocketChannel? _channel;
  StreamSubscription? _sub;
  bool _closed = false;
  int _attempt = 0;

  Future<void> connect() async {
    _stateCtrl.add(SocketStatus.connecting);
    final uri = Uri.parse('$baseWsUrl/ws/device?token=$jwt');
    final channel = WebSocketChannel.connect(uri);
    _channel = channel;
    _sub = channel.stream.listen(
      (message) {
        if (message is String) {
          try {
            final decoded = jsonDecode(message);
            if (decoded is Map<String, dynamic>) {
              if (decoded['type'] == 'revoked') {
                _stateCtrl.add(SocketStatus.revoked);
              } else {
                _stateCtrl.add(SocketStatus.connected);
                _eventCtrl.add(decoded);
              }
            }
          } catch (_) {
            // non-JSON keepalive frame — ignore
          }
        }
      },
      onDone: _onDisconnect,
      onError: (_) => _onDisconnect(),
    );
  }

  void _onDisconnect() {
    _sub?.cancel();
    _channel?.sink.close();
    if (_closed) return;
    _stateCtrl.add(SocketStatus.closed);
    final delay = Duration(seconds: _delaySeconds());
    Timer(delay, () {
      if (!_closed) connect();
    });
  }

  int _delaySeconds() {
    _attempt += 1;
    if (_attempt > 6) _attempt = 6;
    return 1 * _attempt; // 1s, 2s, ..., capped at 6s
  }

  Future<void> dispose() async {
    _closed = true;
    await _sub?.cancel();
    await _channel?.sink.close();
    await _stateCtrl.close();
    await _eventCtrl.close();
  }
}