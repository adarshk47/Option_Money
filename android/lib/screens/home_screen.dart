import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../models/signal.dart';
import '../services/api_service.dart';
import '../services/notification_service.dart';
import 'signals_screen.dart';
import 'watchlist_screen.dart';
import 'pnl_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int _tab = 0;
  Timer? _poller;
  final Set<String> _alerted = {};

  @override
  void initState() {
    super.initState();
    // Poll the backend every 30s; raise a notification on new signals.
    _poller = Timer.periodic(const Duration(seconds: 30), (_) => _checkSignals());
  }

  Future<void> _checkSignals() async {
    try {
      final signals = await context.read<ApiService>().latestSignals();
      for (final s in signals.take(5).where((s) => s.isActionable)) {
        final key = '${s.underlying}-${s.action}-${s.entryPrice}';
        if (_alerted.add(key)) {
          await NotificationService.signalAlert(
            '${s.action} — ${s.underlying}',
            'Entry ${s.entryPrice} | SL ${s.stopLoss} | T1 ${s.target1} '
                '| Confidence ${s.confidence.toStringAsFixed(0)}%',
          );
        }
      }
    } catch (_) {/* offline — retry on next tick */}
  }

  @override
  void dispose() {
    _poller?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final pages = const [WatchlistScreen(), SignalsScreen(), PnlScreen()];
    return Scaffold(
      appBar: AppBar(
        title: const Text('OptionMoney AI'),
        actions: [
          IconButton(
            icon: Icon(NotificationService.voiceEnabled
                ? Icons.record_voice_over
                : Icons.voice_over_off),
            onPressed: () => setState(() => NotificationService.voiceEnabled =
                !NotificationService.voiceEnabled),
          ),
          IconButton(
            icon: const Icon(Icons.logout),
            onPressed: () => context.read<ApiService>().logout(),
          ),
        ],
      ),
      body: pages[_tab],
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: (i) => setState(() => _tab = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.list), label: 'Watchlist'),
          NavigationDestination(icon: Icon(Icons.bolt), label: 'Signals'),
          NavigationDestination(icon: Icon(Icons.currency_rupee), label: 'PnL'),
        ],
      ),
    );
  }
}
