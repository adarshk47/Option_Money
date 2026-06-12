import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_tts/flutter_tts.dart';

/// Local notifications + voice alerts for new AI signals.
class NotificationService {
  static final _plugin = FlutterLocalNotificationsPlugin();
  static final _tts = FlutterTts();
  static bool voiceEnabled = true;

  static Future<void> init() async {
    const android = AndroidInitializationSettings('@mipmap/ic_launcher');
    await _plugin.initialize(const InitializationSettings(android: android));
    await _plugin
        .resolvePlatformSpecificImplementation<
            AndroidFlutterLocalNotificationsPlugin>()
        ?.requestNotificationsPermission();
    await _tts.setSpeechRate(0.5);
  }

  static Future<void> signalAlert(String title, String body) async {
    const details = NotificationDetails(
      android: AndroidNotificationDetails(
        'signals', 'AI Signals',
        importance: Importance.high,
        priority: Priority.high,
      ),
    );
    await _plugin.show(DateTime.now().millisecond, title, body, details);
    if (voiceEnabled) await _tts.speak(title);
  }
}
