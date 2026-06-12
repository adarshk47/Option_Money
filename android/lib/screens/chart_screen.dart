import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../models/signal.dart';
import '../services/api_service.dart';

class ChartScreen extends StatefulWidget {
  final String underlying;
  const ChartScreen({super.key, required this.underlying});

  @override
  State<ChartScreen> createState() => _ChartScreenState();
}

class _ChartScreenState extends State<ChartScreen> {
  List<FlSpot> _closes = [];
  Signal? _signal;
  String _interval = '5min';
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    final api = context.read<ApiService>();
    try {
      final candles =
          await api.candles(widget.underlying, interval: _interval);
      final sig =
          await api.liveSignal(widget.underlying, interval: _interval);
      final spots = <FlSpot>[];
      for (var i = 0; i < candles.length; i++) {
        spots.add(FlSpot(i.toDouble(), (candles[i]['close'] as num).toDouble()));
      }
      if (mounted) {
        setState(() {
          _closes = spots;
          _signal = sig;
          _loading = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.underlying),
        actions: [
          DropdownButton<String>(
            value: _interval,
            items: const ['1min', '5min', '15min']
                .map((e) => DropdownMenuItem(value: e, child: Text(e)))
                .toList(),
            onChanged: (v) {
              if (v != null) {
                _interval = v;
                _load();
              }
            },
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : Column(
              children: [
                if (_signal != null)
                  Card(
                    margin: const EdgeInsets.all(12),
                    child: ListTile(
                      title: Text(_signal!.action,
                          style: TextStyle(
                              color: _signal!.isCall
                                  ? Colors.greenAccent
                                  : _signal!.isPut
                                      ? Colors.redAccent
                                      : Colors.grey,
                              fontWeight: FontWeight.bold)),
                      subtitle: Text(
                          'Conf ${_signal!.confidence.toStringAsFixed(0)}% · '
                          'Entry ${_signal!.entryPrice} · SL ${_signal!.stopLoss} · '
                          'T1 ${_signal!.target1}'),
                    ),
                  ),
                Expanded(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: _closes.isEmpty
                        ? const Center(child: Text('No data'))
                        : LineChart(
                            LineChartData(
                              lineBarsData: [
                                LineChartBarData(
                                  spots: _closes,
                                  isCurved: false,
                                  dotData: const FlDotData(show: false),
                                  color: Colors.tealAccent,
                                ),
                              ],
                              titlesData: const FlTitlesData(
                                topTitles: AxisTitles(),
                                bottomTitles: AxisTitles(),
                              ),
                            ),
                          ),
                  ),
                ),
              ],
            ),
    );
  }
}
