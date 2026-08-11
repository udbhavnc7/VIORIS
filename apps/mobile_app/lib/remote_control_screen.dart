/// Remote control screen (Phase 5.3): on-demand screen mirror + input.
///
/// Mirroring is a peek, never a stream: one frame per button press, throttled
/// to 2s by the gateway. Tap anywhere to click, drag to move, or type into the
/// keyboard field. A live session is required — without one, every control is
/// disabled and 'Start session' opens the approval flow.
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';

class RemoteControlScreen extends StatefulWidget {
  const RemoteControlScreen({super.key});

  @override
  State<RemoteControlScreen> createState() => _RemoteControlScreenState();
}

class _RemoteControlScreenState extends State<RemoteControlScreen> {
  final _textController = TextEditingController();
  bool _busy = false;

  @override
  void dispose() {
    _textController.dispose();
    super.dispose();
  }

  Future<void> _run(Future<void> Function() op) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await op();
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _startSession(AppState state) async {
    await _run(() async {
      final started = await state.requestRemoteControl();
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(started
            ? 'Session request sent — approve it in the Approvals tab'
            : state.error ?? 'Could not request a session'),
      ));
    });
  }

  Future<void> _confirmLock(AppState state) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Lock the laptop now?'),
        content: const Text(
            'This locks the workstation immediately and ends this remote session.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Lock'),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      await _run(state.lockWorkstation);
    }
  }

  void _tapScreen(AppState state, Offset local, Size size) async {
    if (state.remoteSession == null) return;
    // Scale the tap into desktop coordinates (screen space assumed 1:1 by
    // default; the laptop agent treats coordinates as absolute pixels).
    await _run(() =>
        state.sendRemoteAction('move', x: local.dx.toInt(), y: local.dy.toInt()));
    await _run(() =>
        state.sendRemoteAction('left', x: local.dx.toInt(), y: local.dy.toInt()));
  }

  void _type(AppState state) async {
    final text = _textController.text;
    if (text.isEmpty) return;
    await _run(() => state.sendRemoteAction('type', text: text));
    _textController.clear();
  }

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    if (!state.paired) {
      return const Center(child: Text('Pair a device to use remote control'));
    }
    final session = state.remoteSession;
    final liveSession = (session != null && !session.expired) ? session : null;

    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
          child: Row(
            children: [
              Icon(Icons.present_to_all, size: 18,
                  color: liveSession != null ? Colors.green : Colors.grey),
              const SizedBox(width: 6),
              Text(
                liveSession != null
                    ? 'Session live · ${liveSession.ttlSeconds}s left'
                    : 'No active session',
                style: const TextStyle(fontWeight: FontWeight.w600),
              ),
              const Spacer(),
              if (liveSession != null) ...[
                IconButton.filled(
                  tooltip: 'Lock laptop now',
                  onPressed: () => _confirmLock(state),
                  icon: const Icon(Icons.lock),
                ),
                const SizedBox(width: 8),
                FilledButton.tonal(
                  onPressed: () => _run(state.endRemoteSession),
                  child: const Text('End session'),
                ),
              ] else
                FilledButton.icon(
                  onPressed: () => _startSession(state),
                  icon: const Icon(Icons.play_arrow),
                  label: Text(state.remoteRefreshing ? '…' : 'Start session'),
                ),
            ],
          ),
        ),
        const SizedBox(height: 8),
        Expanded(
          child: liveSession != null ? _buildMirror(state) : _buildIdle(state),
        ),
      ],
    );
  }

  Widget _buildIdle(AppState state) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.visibility_off, size: 48, color: Colors.grey),
            const SizedBox(height: 12),
            const Text('Session required for screen mirroring and input.',
                textAlign: TextAlign.center),
            const SizedBox(height: 4),
            Text(state.error ?? '',
                textAlign: TextAlign.center,
                style: TextStyle(color: Theme.of(context).colorScheme.error)),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              onPressed: () => _run(state.refreshRemote),
              icon: const Icon(Icons.refresh),
              label: const Text('Check session'),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildMirror(AppState state) {
    return Column(
      children: [
        Expanded(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: _MirrorView(
              frame: state.remoteFrame,
              busy: state.remoteRefreshing,
              onTapDown: (local, size) => _tapScreen(state, local, size),
              onRefresh: () => _run(state.refreshRemote),
            ),
          ),
        ),
        Padding(
          padding: const EdgeInsets.all(12),
          child: Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _textController,
                  decoration: const InputDecoration(
                    hintText: 'Type text then Send',
                    border: OutlineInputBorder(),
                    isDense: true,
                  ),
                  textInputAction: TextInputAction.send,
                  onSubmitted: (_) => _type(state),
                ),
              ),
              const SizedBox(width: 8),
              IconButton.filled(
                tooltip: 'Send keyboard text',
                onPressed: () => _type(state),
                icon: Icon(_busy ? Icons.hourglass_top : Icons.keyboard_alt),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _MirrorView extends StatelessWidget {
  const _MirrorView({
    required this.frame,
    required this.busy,
    required this.onTapDown,
    required this.onRefresh,
  });

  final Uint8List? frame;
  final bool busy;
  final void Function(Offset local, Size size) onTapDown;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) {
    final img = frame;
    if (img == null) {
      return Container(
        decoration: BoxDecoration(
          color: Colors.grey.shade900,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Center(
          child: busy
              ? const CircularProgressIndicator()
              : FilledButton.icon(
                  onPressed: onRefresh,
                  icon: const Icon(Icons.center_focus_strong),
                  label: const Text('Show screen'),
                ),
        ),
      );
    }
    return LayoutBuilder(
      builder: (context, constraints) {
        return GestureDetector(
          onTapDown: (d) => onTapDown(d.localPosition, constraints.biggest),
          behavior: HitTestBehavior.opaque,
          child: ClipRRect(
            borderRadius: BorderRadius.circular(12),
            child: Stack(
              fit: StackFit.expand,
              children: [
                Image.memory(img, fit: BoxFit.contain),
                if (busy)
                  const Positioned(
                    top: 8,
                    right: 8,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                const Positioned(
                  left: 8,
                  bottom: 8,
                  child: Chip(
                    avatar: Icon(Icons.touch_app, size: 14),
                    label: Text('Tap to click', style: TextStyle(fontSize: 11)),
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}