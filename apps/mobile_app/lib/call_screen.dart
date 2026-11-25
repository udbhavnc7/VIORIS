/// Full-screen in-app call surface (Phase 8.5, item 10a).
///
/// A `ring` event from the gateway puts this over every tab. Answering pulls
/// the opening digest from the same session the web console sees. This is an
/// in-app call over Tailscale — it never dials a phone number. Audio synthesis
/// belongs to the phone, so until streaming is wired this is a text console.
library;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'app_state.dart';

class CallScreen extends StatefulWidget {
  const CallScreen({super.key});

  @override
  State<CallScreen> createState() => _CallScreenState();
}

class _CallScreenState extends State<CallScreen> {
  final _input = TextEditingController();

  @override
  void dispose() {
    _input.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final ring = state.incomingCall;
    if (ring == null) return const SizedBox.shrink();

    final active = state.activeCallId != null;
    final theme = Theme.of(context);

    return Scaffold(
      backgroundColor: const Color(0xFF10121A),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 18),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                    decoration: BoxDecoration(
                      color: const Color(0xFF7B4DFF).withValues(alpha: 0.16),
                      borderRadius: BorderRadius.circular(20),
                    ),
                    child: const Text(
                      'IN-APP CALL · NO PHONE LINE',
                      style: TextStyle(fontSize: 10, letterSpacing: 0.08),
                    ),
                  ),
                  IconButton(
                    tooltip: 'End call',
                    icon: const Icon(Icons.call_end),
                    onPressed: state.callBusy ? null : state.endCall,
                  ),
                ],
              ),
              const SizedBox(height: 28),
              CircleAvatar(
                radius: 52,
                backgroundColor: const Color(0xFF7B4DFF).withValues(alpha: 0.18),
                child: const Icon(Icons.graphic_eq, size: 46, color: Color(0xFFB79BFF)),
              ),
              const SizedBox(height: 20),
              Text(
                active ? 'Vioris' : '${ring.callerName} is calling',
                textAlign: TextAlign.center,
                style: theme.textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w600),
              ),
              const SizedBox(height: 8),
              Text(
                active
                    ? 'call ${ring.callId}'
                    : (ring.reason.isEmpty ? 'A proactive digest is ready.' : ring.reason),
                textAlign: TextAlign.center,
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: theme.colorScheme.onSurface.withValues(alpha: 0.65),
                ),
              ),
              const SizedBox(height: 20),
              if (active)
                Expanded(
                  child: Container(
                    padding: const EdgeInsets.all(14),
                    decoration: BoxDecoration(
                      color: Colors.white.withValues(alpha: 0.035),
                      borderRadius: BorderRadius.circular(14),
                      border: Border.all(color: Colors.white.withValues(alpha: 0.07)),
                    ),
                    child: state.callTranscript.isEmpty
                        ? const Center(
                            child: Text('Waiting for the first turn...'),
                          )
                        : ListView.builder(
                            itemCount: state.callTranscript.length,
                            itemBuilder: (context, index) => Padding(
                              padding: const EdgeInsets.only(bottom: 12),
                              child: Text(state.callTranscript[index]),
                            ),
                          ),
                  ),
                )
              else
                Container(
                  padding: const EdgeInsets.all(14),
                  decoration: BoxDecoration(
                    color: Colors.white.withValues(alpha: 0.035),
                    borderRadius: BorderRadius.circular(14),
                    border: Border.all(color: Colors.white.withValues(alpha: 0.07)),
                  ),
                  child: Text(
                    ring.digestPreview.isEmpty
                        ? 'No preview was sent with this ring.'
                        : ring.digestPreview,
                    style: theme.textTheme.bodyMedium,
                  ),
                ),
              const SizedBox(height: 18),
              if (active)
                Row(
                  children: [
                    Expanded(
                      child: TextField(
                        controller: _input,
                        enabled: !state.callBusy,
                        decoration: const InputDecoration(
                          hintText: 'Say something, e.g. “yes, go on”',
                          border: OutlineInputBorder(),
                        ),
                        onSubmitted: (value) {
                          state.sendCallMessage(value);
                          _input.clear();
                        },
                      ),
                    ),
                    const SizedBox(width: 8),
                    FilledButton(
                      onPressed: state.callBusy
                          ? null
                          : () {
                              state.sendCallMessage(_input.text);
                              _input.clear();
                            },
                      child: const Text('Send'),
                    ),
                  ],
                )
              else
                Row(
                  children: [
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: state.callBusy ? null : state.endCall,
                        icon: const Icon(Icons.call_end),
                        label: const Text('Decline'),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: FilledButton.icon(
                        onPressed: state.callBusy ? null : state.answerCall,
                        icon: const Icon(Icons.call),
                        label: Text(state.callBusy ? 'Answering...' : 'Answer'),
                      ),
                    ),
                  ],
                ),
              const SizedBox(height: 12),
              Text(
                'Replies are typed for now. Nothing is sent until you tap Send, and every action still goes through approval.',
                textAlign: TextAlign.center,
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurface.withValues(alpha: 0.55),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
