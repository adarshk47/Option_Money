import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/api_service.dart';
import 'chart_screen.dart';

class WatchlistScreen extends StatefulWidget {
  const WatchlistScreen({super.key});

  @override
  State<WatchlistScreen> createState() => _WatchlistScreenState();
}

class _WatchlistScreenState extends State<WatchlistScreen> {
  Map<String, dynamic> _overview = {};
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    try {
      final data = await context.read<ApiService>().marketOverview();
      if (mounted) setState(() => {_overview = data, _loading = false});
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  Color _actionColor(String action) {
    if (action.contains('CE')) return Colors.greenAccent;
    if (action.contains('PE')) return Colors.redAccent;
    return Colors.grey;
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView(
        children: _overview.entries.map((e) {
          final v = e.value as Map<String, dynamic>;
          final action = (v['action'] ?? '—').toString();
          return Card(
            margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            child: ListTile(
              title: Text(e.key,
                  style: const TextStyle(fontWeight: FontWeight.bold)),
              subtitle: Text(action,
                  style: TextStyle(color: _actionColor(action))),
              trailing: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(v['ltp']?.toStringAsFixed(1) ?? '—',
                      style: const TextStyle(fontSize: 16)),
                  Text('${(v['confidence'] ?? 0).toStringAsFixed(0)}% conf',
                      style: const TextStyle(fontSize: 12)),
                ],
              ),
              onTap: () => Navigator.push(
                context,
                MaterialPageRoute(
                    builder: (_) => ChartScreen(underlying: e.key)),
              ),
            ),
          );
        }).toList(),
      ),
    );
  }
}
