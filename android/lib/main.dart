import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'services/api_service.dart';
import 'services/notification_service.dart';
import 'screens/login_screen.dart';
import 'screens/home_screen.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await NotificationService.init();
  runApp(
    ChangeNotifierProvider(
      create: (_) => ApiService()..loadSaved(),
      child: const OptionMoneyApp(),
    ),
  );
}

class OptionMoneyApp extends StatelessWidget {
  const OptionMoneyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'OptionMoney AI',
      debugShowCheckedModeBanner: false,
      themeMode: ThemeMode.dark, // dark mode by default
      darkTheme: ThemeData(
        brightness: Brightness.dark,
        colorScheme: ColorScheme.fromSeed(
          seedColor: Colors.teal,
          brightness: Brightness.dark,
        ),
        scaffoldBackgroundColor: const Color(0xFF101418),
        useMaterial3: true,
      ),
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.teal),
        useMaterial3: true,
      ),
      home: Consumer<ApiService>(
        builder: (context, api, _) =>
            api.isLoggedIn ? const HomeScreen() : const LoginScreen(),
      ),
    );
  }
}
