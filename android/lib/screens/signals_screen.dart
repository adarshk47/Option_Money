import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../models/signal.dart';
import '../services/api_service.dart';

class SignalsScreen extends StatefulWidget {
  const SignalsScreen({super.key});

  @override
  State<SignalsScreen> createState() => _SignalsScreenState();
}

class _SignalsScreenState extends State<SignalsScreen> {
  List<Signal> _signals = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    try {
      final data = await context.read<ApiService>().latestSignals();
      if (mounted) setState(() => {_signals = data, _loading = false});
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_signals.isEmpty) {
      return const Center(child: Text('No signals yet — engine warming up'));
    }
    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView.builder(
        itemCount: _signals.length,
        itemBuilder: (context, i) {
          final s = _signals[i];
          final color = s.isCall
              ? Colors.greenAccent
              : s.isPut
                  ? Colors.redAccent
                  : Colors.grey;
          return Card(
            margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            child: ExpansionTile(
              leading: Icon(
                  s.isCall
                      ? Icons.trending_up
                      : s.isPut
                          ? Icons.trending_down
                          : Icons.remove,
                  color: color),
              title: Text('${s.action} — ${s.underlying}',
                  style: TextStyle(color: color, fontWeight: FontWeight.bold)),
              subtitle: Text(
                  '${s.timeframe} · ${s.confidence.toStringAsFixed(0)}% · ${s.riskLevel}'),
              children: [
                Padding(
                  padding: const EdgeInsets.all(12),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Entry: ${s.entryPrice}   SL: ${s.stopLoss}'),
                      Text('T1: ${s.target1}   T2: ${s.target2}'),
                      const SizedBox(height: 6),
                      Text(s.reasoning,
                          style: const TextStyle(fontSize: 12)),
                    ],
                  ),
                ),
              ],
            ),
          );
        },
      ),
    );
  }
}
