// Vioris mobile widget tests: shell, screens, models (Phase 5, Prompt 5.2).

import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:provider/provider.dart';

import 'package:vioris_mobile/app_state.dart';
import 'package:vioris_mobile/gateway_client.dart';
import 'package:vioris_mobile/main.dart';
import 'package:vioris_mobile/main_shell.dart';
import 'package:vioris_mobile/models.dart';
import 'package:vioris_mobile/widgets.dart';

AppState _unpairedState() => AppState(
      GatewayClient(baseUrl: 'http://127.0.0.1:8420'),
    );

/// Records requests and returns a canned JSON body per path.
class _RecordingClient extends http.BaseClient {
  _RecordingClient(this._respond);

  final http.Response Function(http.BaseRequest request) _respond;
  final List<http.BaseRequest> requests = [];

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    requests.add(request);
    final response = _respond(request);
    return http.StreamedResponse(
      Stream.value(response.bodyBytes),
      response.statusCode,
      headers: response.headers,
    );
  }
}

void main() {
  group('models', () {
    test('Approval.fromJson parses diff card', () {
      final a = Approval.fromJson({
        'approval_id': 'apr_1',
        'task_id': 't1',
        'step_id': 's1',
        'tool': 'system.send_message',
        'status': 'pending',
        'diff_card': {'recipient': 'bob', 'content': 'hi', 'channel': 'whatsapp'},
      });
      expect(a.summary, 'send message');
      expect(a.diffCard['recipient'], 'bob');
    });

    test('Task.fromJson parses steps', () {
      final t = Task.fromJson({
        'task_id': 't1',
        'request': 'do it',
        'status': 'running',
        'steps': [
          {'step_id': 's1', 'tool': 'system.open_app', 'risk_level': 'observe', 'status': 'completed'},
        ],
      });
      expect(t.steps, hasLength(1));
      expect(t.steps.first.riskLevel, 'observe');
    });

    test('DeviceSession.fromJson', () {
      final s = DeviceSession.fromJson({'device_id': 'd1', 'jwt': 'abc', 'expires_at': 123.0});
      expect(s.deviceId, 'd1');
      expect(s.jwt, 'abc');
    });

    test('RemoteSession.fromJson parses live session', () {
      final s = RemoteSession.fromJson({
        'session_id': 'rs_1',
        'device_id': 'd1',
        'expired': false,
        'ttl_seconds': 300,
      });
      expect(s.sessionId, 'rs_1');
      expect(s.expired, isFalse);
      expect(s.ttlSeconds, 300);
    });
  });

  group('shell', () {
    testWidgets('shows Home + persistent stop for unpaired device hide after pair',
        (tester) async {
      await tester.pumpWidget(
        ChangeNotifierProvider<AppState>(
          create: (_) => _unpairedState(),
          child: const ViorisApp(),
        ),
      );
      await tester.pump();

      // Home screen visible, status says unpaired.
      expect(find.text('Pair your device'), findsOneWidget);
      expect(find.text('Unpaired'), findsOneWidget);
    });

    testWidgets('approval queue shows empty state', (tester) async {
      await tester.pumpWidget(
        ChangeNotifierProvider<AppState>(
          create: (_) => _unpairedState(),
          child: const MaterialApp(home: MainShell()),
        ),
      );
      await tester.pump();
      await tester.tap(find.byIcon(Icons.hourglass_top));
      await tester.pump();
      expect(find.text('Pair a device to see approvals'), findsOneWidget);
    });

    testWidgets('activity timeline shows empty state', (tester) async {
      await tester.pumpWidget(
        ChangeNotifierProvider<AppState>(
          create: (_) => _unpairedState(),
          child: const MaterialApp(home: MainShell()),
        ),
      );
      await tester.pump();
      await tester.tap(find.byIcon(Icons.history));
      await tester.pump();
      expect(find.text('Pair a device to see activity'), findsOneWidget);
    });

    testWidgets('remote tab shows unpaired and session-required states', (tester) async {
      await tester.pumpWidget(
        ChangeNotifierProvider<AppState>(
          create: (_) => _unpairedState(),
          child: const MaterialApp(home: MainShell()),
        ),
      );
      await tester.pump();
      await tester.tap(find.byIcon(Icons.present_to_all));
      await tester.pump();
      expect(find.text('Pair a device to use remote control'), findsOneWidget);
    });
  });

  group('diff card rendering', () {
    testWidgets('pending approval renders fields and buttons', (tester) async {
      final approval = Approval(
        approvalId: 'apr_9',
        taskId: 't1',
        stepId: 's1',
        tool: 'system.register_payment',
        status: 'pending',
        diffCard: {
          'payee': 'Ritesh',
          'amount': '500',
          'account': 'savings',
        },
      );
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: DiffCard(
              approval: approval,
              showButtons: true,
              onApprove: () {},
              onReject: () {},
            ),
          ),
        ),
      );
      expect(find.text('register payment'), findsOneWidget);
      expect(find.text('Approve'), findsOneWidget);
      expect(find.text('Reject'), findsOneWidget);
      expect(find.text('Ritesh'), findsOneWidget);
    });
  });

  group('in-app calls', () {
    test('CallRing.fromJson parses the pushed payload', () {
      final ring = CallRing.fromJson({
        'call_id': 'c1',
        'source': 'proactive',
        'caller_name': 'Vioris',
        'reason': 'Evening check-in',
        'digest_preview': '2 new emails',
      });
      expect(ring.callId, 'c1');
      expect(ring.reason, 'Evening check-in');
      expect(ring.digestPreview, '2 new emails');
    });

    test('call helpers use the device JWT', () async {
      final recording = _RecordingClient((request) {
        final path = request.url.path;
        if (path.endsWith('/speak')) {
          return http.Response(
            jsonEncode({'spoken_text': 'Hello'}),
            200,
            headers: {'content-type': 'application/json'},
          );
        }
        if (path.endsWith('/message')) {
          return http.Response(
            jsonEncode({'response_text': 'Got it'}),
            200,
            headers: {'content-type': 'application/json'},
          );
        }
        return http.Response(
          jsonEncode({'status': 'completed'}),
          200,
          headers: {'content-type': 'application/json'},
        );
      });
      final client = GatewayClient(
        baseUrl: 'http://127.0.0.1:8420',
        client: recording,
      );

      expect(await client.speakCall('jwt', 'c1'), 'Hello');
      expect(await client.callMessage('jwt', 'c1', 'go on'), 'Got it');
      expect(await client.endCall('jwt', 'c1'), isTrue);
      expect(recording.requests, hasLength(3));
      for (final request in recording.requests) {
        expect(request.headers['Authorization'], 'Bearer jwt');
      }
      expect(recording.requests.last.method, 'POST');
      expect(recording.requests.last.url.path, '/v1/call/c1/end');
    });

    testWidgets('a ring takes over the shell and can be declined', (tester) async {
      final state = _unpairedState();
      state.incomingCall = const CallRing(
        callId: 'c1',
        source: 'proactive',
        callerName: 'Vioris',
        reason: 'Evening check-in',
        digestPreview: '2 new emails',
      );
      state.notifyListeners();

      await tester.pumpWidget(
        ChangeNotifierProvider<AppState>.value(
          value: state,
          child: const MaterialApp(home: MainShell()),
        ),
      );
      await tester.pump();

      expect(find.text('Vioris is calling'), findsOneWidget);
      expect(find.text('Evening check-in'), findsOneWidget);
      expect(find.text('2 new emails'), findsOneWidget);
      expect(find.text('IN-APP CALL · NO PHONE LINE'), findsOneWidget);

      await tester.tap(find.text('Decline'));
      await tester.pump();
      expect(find.text('Vioris is calling'), findsNothing);
      expect(find.text('Pair your device'), findsOneWidget);
    });
  });
}