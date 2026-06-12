class Signal {
  final String underlying;
  final String timeframe;
  final String action;
  final double confidence;
  final double entryPrice;
  final double stopLoss;
  final double target1;
  final double target2;
  final String riskLevel;
  final String reasoning;

  Signal({
    required this.underlying,
    required this.timeframe,
    required this.action,
    required this.confidence,
    required this.entryPrice,
    required this.stopLoss,
    required this.target1,
    required this.target2,
    required this.riskLevel,
    required this.reasoning,
  });

  factory Signal.fromJson(Map<String, dynamic> json) {
    double d(dynamic v) => (v is num) ? v.toDouble() : 0.0;
    final reasoning = json['reasoning'];
    return Signal(
      underlying: json['underlying'] ?? '',
      timeframe: json['timeframe'] ?? '',
      action: json['action'] ?? '',
      confidence: d(json['confidence']),
      entryPrice: d(json['entry_price']),
      stopLoss: d(json['stop_loss']),
      target1: d(json['target1']),
      target2: d(json['target2']),
      riskLevel: json['risk_level'] ?? '',
      reasoning: reasoning is List ? reasoning.join('; ') : (reasoning ?? ''),
    );
  }

  bool get isCall => action.contains('CE');
  bool get isPut => action.contains('PE');
  bool get isActionable => isCall || isPut;
}
