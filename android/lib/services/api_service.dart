import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import '../models/signal.dart';

/// Talks to the FastAPI backend (api/server.py).
class ApiService extends ChangeNotifier {
  String baseUrl = '';
  String _token = '';

  bool get isLoggedIn => _token.isNotEmpty && baseUrl.isNotEmpty;

  Map<String, String> get _headers => {
        'Authorization': 'Bearer $_token',
        'Content-Type': 'application/json',
      };

  Future<void> loadSaved() async {
    final prefs = await SharedPreferences.getInstance();
    baseUrl = prefs.getString('baseUrl') ?? '';
    _token = prefs.getString('token') ?? '';
    notifyListeners();
  }

  Future<bool> login(String serverUrl, String secretKey) async {
    try {
      baseUrl = serverUrl.replaceAll(RegExp(r'/+$'), '');
      final resp = await http
          .post(Uri.parse('$baseUrl/auth/login'),
              headers: {'Content-Type': 'application/json'},
              body: jsonEncode({'secret_key': secretKey}))
          .timeout(const Duration(seconds: 10));
      if (resp.statusCode != 200) return false;
      _token = jsonDecode(resp.body)['token'];
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('baseUrl', baseUrl);
      await prefs.setString('token', _token);
      notifyListeners();
      return true;
    } catch (_) {
      return false;
    }
  }

  Future<void> logout() async {
    _token = '';
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('token');
    notifyListeners();
  }

  Future<dynamic> _get(String path) async {
    final resp = await http
        .get(Uri.parse('$baseUrl$path'), headers: _headers)
        .timeout(const Duration(seconds: 15));
    if (resp.statusCode == 401) {
      await logout();
      throw Exception('Session expired');
    }
    if (resp.statusCode != 200) throw Exception('HTTP ${resp.statusCode}');
    return jsonDecode(resp.body);
  }

  Future<Map<String, dynamic>> marketOverview() async =>
      Map<String, dynamic>.from(await _get('/market/overview'));

  Future<List<Signal>> latestSignals() async {
    final data = await _get('/signals/latest?limit=30') as List;
    return data.map((e) => Signal.fromJson(e)).toList();
  }

  Future<Signal> liveSignal(String name, {String interval = '5min'}) async =>
      Signal.fromJson(await _get('/signals/live/$name?interval=$interval'));

  Future<Map<String, dynamic>> pnl() async =>
      Map<String, dynamic>.from(await _get('/portfolio/pnl'));

  Future<List<dynamic>> candles(String name,
          {String interval = '5min'}) async =>
      await _get('/market/candles/$name?interval=$interval') as List;
}
