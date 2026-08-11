/// App shell: three screens + persistent Stop Everything from every tab.
library;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../widgets.dart';
import 'activity_timeline_screen.dart';
import 'approval_queue_screen.dart';
import 'home_screen.dart';
import 'remote_control_screen.dart';

class MainShell extends StatefulWidget {
  const MainShell({super.key});

  @override
  State<MainShell> createState() => _MainShellState();
}

class _MainShellState extends State<MainShell> {
  int _index = 0;

  static const _screens = [
    HomeScreen(),
    ApprovalQueueScreen(),
    ActivityTimelineScreen(),
    RemoteControlScreen(),
  ];

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    return Scaffold(
      body: IndexedStack(index: _index, children: _screens),
      bottomNavigationBar: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // Persistent emergency control — present on every tab.
          if (state.paired)
            StopEverythingBar(
              busy: state.stopInFlight,
              onPressed: _confirmStop,
            ),
          NavigationBar(
            selectedIndex: _index,
            onDestinationSelected: (i) => setState(() => _index = i),
            destinations: const [
              NavigationDestination(icon: Icon(Icons.home), label: 'Home'),
              NavigationDestination(icon: Icon(Icons.hourglass_top), label: 'Approvals'),
              NavigationDestination(icon: Icon(Icons.history), label: 'Activity'),
              NavigationDestination(icon: Icon(Icons.present_to_all), label: 'Remote'),
            ],
          ),
        ],
      ),
    );
  }

  Future<void> _confirmStop() async {
    final state = context.read<AppState>();
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Stop everything?'),
        content: const Text(
            'This halts every running task immediately. No new step will fire.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Stop'),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      await state.stopEverything();
    }
  }
}