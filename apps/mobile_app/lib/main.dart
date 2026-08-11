/// Vioris mobile — phone control center (Phase 5, Prompt 5.2).
///
/// Three screens (Home, Approval Queue, Activity Timeline) and a persistent
/// Stop Everything control, all over the paired gateway WebSocket.
library;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'app_state.dart';
import 'gateway_client.dart';
import 'main_shell.dart';

void main() {
  runApp(
    ChangeNotifierProvider(
      create: (_) => AppState(defaultGatewayClient()),
      child: const ViorisApp(),
    ),
  );
}

class ViorisApp extends StatelessWidget {
  const ViorisApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Vioris',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF7B4DFF), // electric violet
          brightness: Brightness.dark,
        ),
        scaffoldBackgroundColor: const Color(0xFF11131A), // dark graphite
      ),
      home: const MainShell(),
    );
  }
}