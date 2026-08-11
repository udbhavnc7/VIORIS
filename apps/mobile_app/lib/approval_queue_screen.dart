/// Approval Queue: pending execute/critical steps with Approve/Reject.
library;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../widgets.dart';

class ApprovalQueueScreen extends StatelessWidget {
  const ApprovalQueueScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final queue = state.approvals.where((a) => a.status == 'pending').toList();

    if (!state.paired) {
      return const Center(child: Text('Pair a device to see approvals'));
    }
    if (queue.isEmpty) {
      return const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.done_all, size: 40, color: Colors.grey),
            SizedBox(height: 8),
            Text('No pending approvals'),
          ],
        ),
      );
    }
    return RefreshIndicator(
      onRefresh: state.refreshAll,
      child: ListView.builder(
        padding: const EdgeInsets.all(12),
        itemCount: queue.length,
        itemBuilder: (context, i) {
          final approval = queue[i];
          return DiffCard(
            approval: approval,
            showButtons: true,
            onApprove: () => state.decide(approval, true),
            onReject: () => state.decide(approval, false),
          );
        },
      ),
    );
  }
}