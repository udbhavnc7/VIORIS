/// Home screen: voice orb, push-to-talk, live connection status.
library;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../gateway_client.dart';
import '../gateway_socket.dart';
import '../widgets.dart';

class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key});

  Future<void> _startPairing(BuildContext context) async {
    final state = context.read<AppState>();
    final name = await showDialog<String>(
      context: context,
      builder: (ctx) => const _PairNameDialog(),
    );
    if (name == null || name.trim().isEmpty) return;
    try {
      final tokenResult = await state.client.startPairing(name.trim());
      // The token would be QR-scanned by the phone; simulate entry for demo.
      if (!context.mounted) return;
      _promptTokenScan(context, state, tokenResult);
    } on GatewayException catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message)));
      }
    }
  }

  void _promptTokenScan(
      BuildContext context, AppState state, Map<String, dynamic> tokenResult) {
    showModalBottomSheet<void>(
      context: context,
      builder: (ctx) => _PairingSheet(
        deviceId: tokenResult['device_id'] as String,
        token: tokenResult['pairing_token'] as String,
        onUs: () => state.pairWithToken(tokenResult['device_id'] as String,
            tokenResult['pairing_token'] as String),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    return Column(
      children: [
        const SizedBox(height: 24),
        VoiceOrb(listening: state.paired),
        const SizedBox(height: 16),
        Text(
          state.paired ? 'Vioris' : 'Pair your device',
          style: Theme.of(context).textTheme.headlineMedium,
        ),
        const SizedBox(height: 4),
        _StatusLine(state: state),
        const Spacer(),
        if (!state.paired)
          FilledButton.icon(
            onPressed: () => _startPairing(context),
            icon: const Icon(Icons.qr_code_scanner),
            label: const Text('Pair device'),
          )
        else
          FilledButton.icon(
            onPressed: () => state.refreshAll(),
            icon: const Icon(Icons.refresh),
            label: const Text('Refresh'),
          ),
        const SizedBox(height: 24),
      ],
    );
  }
}

class _StatusLine extends StatelessWidget {
  const _StatusLine({required this.state});

  final AppState state;

  @override
  Widget build(BuildContext context) {
    final connected = state.socketStatus == SocketStatus.connected;
    final color = connected ? Colors.green : (state.paired ? Colors.amber : Colors.grey);
    final label = state.paired
        ? (connected ? 'Connected' : 'Connecting…')
        : 'Unpaired';
    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        Icon(Icons.circle, color: color, size: 12),
        const SizedBox(width: 6),
        Text(label, style: const TextStyle(fontSize: 13)),
      ],
    );
  }
}

class _PairNameDialog extends StatelessWidget {
  const _PairNameDialog();

  @override
  Widget build(BuildContext context) {
    final controller = TextEditingController(text: 'my phone');
    return AlertDialog(
      title: const Text('Name this device'),
      content: TextField(controller: controller, autofocus: true),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context, controller.text),
          child: const Text('Continue'),
        ),
      ],
    );
  }
}

class _PairingSheet extends StatelessWidget {
  const _PairingSheet({
    required this.deviceId,
    required this.token,
    required this.onUs,
  });

  final String deviceId;
  final String token;
  final VoidCallback onUs;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Text('Scan this on the phone within 10 minutes',
              style: TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(height: 12),
          SelectableText(token, style: const TextStyle(fontFamily: 'monospace', fontSize: 12)),
          const SizedBox(height: 12),
          // Demo path: same device exchanging its own token.
          FilledButton(onPressed: onUs, child: const Text('Exchange (demo)')),
          const SizedBox(height: 8),
        ],
      ),
    );
  }
}