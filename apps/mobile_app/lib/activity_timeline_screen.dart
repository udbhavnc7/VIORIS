/// Activity Timeline: every task/step/approval event with timestamps.
library;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../models.dart';

class ActivityTimelineScreen extends StatelessWidget {
  const ActivityTimelineScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();

    if (!state.paired) {
      return const Center(child: Text('Pair a device to see activity'));
    }
    if (state.activity.isEmpty) {
      return const Center(child: Text('No activity yet'));
    }
    return RefreshIndicator(
      onRefresh: state.refreshAll,
      child: ListView.builder(
        padding: const EdgeInsets.all(12),
        itemCount: state.activity.length,
        itemBuilder: (context, i) {
          final event = state.activity[i];
          return _ActivityTile(event: event);
        },
      ),
    );
  }
}

class _ActivityTile extends StatelessWidget {
  const _ActivityTile({required this.event});

  final ActivityEvent event;

  @override
  Widget build(BuildContext context) {
    final icon = switch (event.action) {
      'planned_step' => Icons.rule,
      'approved' => Icons.check_circle,
      'rejected' => Icons.cancel,
      'requested_approval' => Icons.hourglass_top,
      'stopped' || 'cancelled' => Icons.stop_circle,
      'state_transition' => Icons.sync,
      _ => Icons.history,
    };
    final when = _friendlyTime(event.createdAt);

    return ListTile(
      leading: Icon(icon, size: 20),
      title: Text(event.action),
      subtitle: Text('${event.actor}${event.taskId != null ? ' · ${event.taskId}' : ''}'),
      trailing: Text(when, style: const TextStyle(fontSize: 11, color: Colors.grey)),
      dense: true,
    );
  }

  String _friendlyTime(String iso) {
    if (iso.isEmpty) return '';
    // The task-runner stores UTC "YYYY-MM-DD HH:MM:SS" (SQLite datetime('now')).
    final raw = iso.replaceAll('T', ' ').replaceAll('Z', '');
    if (raw.length < 19) return iso;
    final time = raw.substring(11, 16);
    final date = raw.substring(5, 10);
    return '$date $time';
  }
}