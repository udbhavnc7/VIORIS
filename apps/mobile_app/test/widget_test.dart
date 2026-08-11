// Vioris mobile widget tests: shell, screens, models (Phase 5, Prompt 5.2).

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
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
}