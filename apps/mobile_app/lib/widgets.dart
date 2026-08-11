/// Shared widgets for the Vioris mobile UI (Phase 5 design system).
library;

import 'package:flutter/material.dart';

import '../models.dart';

/// Circular voice indicator: idle (dim), listening (accent pulse).
class VoiceOrb extends StatelessWidget {
  const VoiceOrb({super.key, this.listening = false, this.size = 120});

  final bool listening;
  final double size;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final color = listening ? scheme.primary : scheme.onSurfaceVariant;
    return AnimatedContainer(
      duration: const Duration(milliseconds: 300),
      width: size,
      height: size,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: color.withValues(alpha: 0.12),
        border: Border.all(color: color, width: 2),
      ),
      child: Center(
        child: Icon(
          listening ? Icons.mic : Icons.mic_none,
          color: color,
          size: size * 0.35,
        ),
      ),
    );
  }
}

/// Color-coded risk badge: observe/prepare/execute/critical.
class RiskBadge extends StatelessWidget {
  const RiskBadge({super.key, required this.risk});

  final String risk;

  @override
  Widget build(BuildContext context) {
    final fallback = Theme.of(context).colorScheme;
    final (label, color) = switch (risk) {
      'observe' => ('Observe', Colors.green),
      'prepare' => ('Prepare', Colors.blue),
      'execute' => ('Execute', Colors.orange),
      'critical' => ('Critical', Colors.red),
      _ => (risk, fallback.outline),
    };
    return Chip(
      label: Text(label, style: TextStyle(fontSize: 11, color: color)),
      visualDensity: VisualDensity.compact,
      backgroundColor: color.withValues(alpha: 0.15),
      side: BorderSide(color: color),
      padding: const EdgeInsets.symmetric(horizontal: 4),
    );
  }
}

/// A diff card for an execute/critical step: tool + exact details.
class DiffCard extends StatelessWidget {
  const DiffCard({super.key, required this.approval, this.showButtons = false, this.onApprove, this.onReject});

  final Approval approval;
  final bool showButtons;
  final VoidCallback? onApprove;
  final VoidCallback? onReject;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final rows = approval.diffCard.entries
        .map((e) => _FieldRow(label: e.key, value: '${e.value}'))
        .toList(growable: true);
    if (rows.isEmpty) {
      rows.add(_FieldRow(label: 'action', value: approval.summary));
    }

    return Card(
      color: scheme.surfaceContainerHighest,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(child: Text(approval.summary, style: const TextStyle(fontWeight: FontWeight.w600))),
                const SizedBox(width: 8),
                const RiskBadge(risk: 'execute'),
              ],
            ),
            const SizedBox(height: 8),
            ...rows,
            if (showButtons) ...[
              const SizedBox(height: 12),
              Row(
                children: [
                  Expanded(
                    child: FilledButton.tonal(
                      onPressed: onReject,
                      child: const Text('Reject'),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: FilledButton(
                      onPressed: onApprove,
                      style: FilledButton.styleFrom(backgroundColor: scheme.primary),
                      child: const Text('Approve'),
                    ),
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _FieldRow extends StatelessWidget {
  const _FieldRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 90,
            child: Text(label, style: TextStyle(color: Colors.grey.shade500, fontSize: 12)),
          ),
          Expanded(
            child: Text(
              value,
              style: const TextStyle(fontSize: 13),
              maxLines: 3,
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ),
    );
  }
}

/// Persistent red "Stop Everything" bar — reachable from every screen.
class StopEverythingBar extends StatelessWidget {
  const StopEverythingBar({super.key, required this.onPressed, required this.busy});

  final VoidCallback onPressed;
  final bool busy;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 4, 12, 8),
        child: Material(
          color: scheme.error.withValues(alpha: 0.15),
          borderRadius: BorderRadius.circular(12),
          child: InkWell(
            borderRadius: BorderRadius.circular(12),
            onTap: busy ? null : onPressed,
            child: Padding(
              padding: const EdgeInsets.all(10),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Icon(
                    busy ? Icons.hourglass_top : Icons.stop_circle,
                    color: scheme.error,
                    size: 20,
                  ),
                  const SizedBox(width: 8),
                  Text(
                    busy ? 'Stopping...' : 'STOP EVERYTHING',
                    style: TextStyle(color: scheme.error, fontWeight: FontWeight.bold),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}