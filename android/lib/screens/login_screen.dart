import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../services/api_service.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _serverCtrl = TextEditingController(text: 'http://192.168.1.10:8000');
  final _keyCtrl = TextEditingController();
  bool _busy = false;
  String? _error;

  Future<void> _login() async {
    setState(() => {_busy = true, _error = null});
    final ok = await context
        .read<ApiService>()
        .login(_serverCtrl.text.trim(), _keyCtrl.text.trim());
    if (!ok && mounted) {
      setState(() {
        _busy = false;
        _error = 'Login failed — check server URL and access key';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.candlestick_chart, size: 72, color: Colors.teal),
              const SizedBox(height: 12),
              Text('OptionMoney AI',
                  style: Theme.of(context).textTheme.headlineMedium),
              const Text('AI Options Buying Signals'),
              const SizedBox(height: 32),
              TextField(
                controller: _serverCtrl,
                decoration: const InputDecoration(
                  labelText: 'Server URL',
                  border: OutlineInputBorder(),
                  prefixIcon: Icon(Icons.dns),
                ),
              ),
              const SizedBox(height: 16),
              TextField(
                controller: _keyCtrl,
                obscureText: true,
                decoration: const InputDecoration(
                  labelText: 'Access key',
                  border: OutlineInputBorder(),
                  prefixIcon: Icon(Icons.key),
                ),
              ),
              const SizedBox(height: 20),
              if (_error != null)
                Text(_error!, style: const TextStyle(color: Colors.redAccent)),
              const SizedBox(height: 8),
              SizedBox(
                width: double.infinity,
                height: 48,
                child: FilledButton(
                  onPressed: _busy ? null : _login,
                  child: _busy
                      ? const CircularProgressIndicator()
                      : const Text('Login'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
