import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/api_service.dart';

class PnlScreen extends StatefulWidget {
  const PnlScreen({super.key});

  @override
  State<PnlScreen> createState() => _PnlScreenState();
}

class _PnlScreenState extends State<PnlScreen> {
  Map<String, dynamic>? _snap;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    try {
      final data = await context.read<ApiService>().pnl();
      if (mounted) setState(() => _snap = data);
    } catch (_) {}
  }

  Widget _metric(String label, dynamic value, {Color? color}) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          children: [
            Text(label, style: const TextStyle(fontSize: 12)),
            const SizedBox(height: 4),
            Text('$value',
                style: TextStyle(
                    fontSize: 20, fontWeight: FontWeight.bold, color: color)),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final s = _snap;
    if (s == null) return const Center(child: CircularProgressIndicator());
    final total = (s['total_pnl_today'] ?? 0) as num;
    final positions = (s['open_positions'] ?? []) as List;
    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView(
        padding: const EdgeInsets.all(12),
        children: [
          GridView.count(
            crossAxisCount: 2,
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            childAspectRatio: 2.2,
            children: [
              _metric('Total today', '₹$total',
                  color: total >= 0 ? Colors.greenAccent : Colors.redAccent),
              _metric('Realised', '₹${s['realised_pnl_today']}'),
              _metric('Unrealised', '₹${s['unrealised_pnl']}'),
              _metric('Trades today', s['trades_today']),
            ],
          ),
          const SizedBox(height: 12),
          const Text('Open positions',
              style: TextStyle(fontWeight: FontWeight.bold)),
          ...positions.map((p) => Card(
                child: ListTile(
                  title: Text(p['symbol'] ?? ''),
                  subtitle: Text(
                      'Entry ₹${p['entry_price']} · Qty ${p['quantity']}'),
                  trailing: Text('₹${(p['pnl'] as num).toStringAsFixed(0)}',
                      style: TextStyle(
                          color: (p['pnl'] as num) >= 0
                              ? Colors.greenAccent
                              : Colors.redAccent)),
                ),
              )),
          if (positions.isEmpty)
            const Padding(
              padding: EdgeInsets.all(24),
              child: Center(child: Text('No open positions')),
            ),
        ],
      ),
    );
  }
}
