# Building the Android APK (Flutter)

## Prerequisites
- Flutter SDK 3.x (`flutter doctor` must be clean)
- Android Studio / Android SDK (API 34)

## Steps

```bash
cd android

# 1. Generate platform folders (first time only — this repo ships lib/ + pubspec)
flutter create . --org com.optionmoney --project-name option_money

# 2. Install packages
flutter pub get

# 3. Run on a connected device/emulator
flutter run

# 4. Release APK
flutter build apk --release
# Output: build/app/outputs/flutter-apk/app-release.apk

# Smaller per-device APKs:
flutter build apk --release --split-per-abi
```

## Required Android permissions
`flutter create` writes `android/app/src/main/AndroidManifest.xml`. Add inside
`<manifest>`:

```xml
<uses-permission android:name="android.permission.INTERNET"/>
<uses-permission android:name="android.permission.POST_NOTIFICATIONS"/>
```

If your FastAPI server runs over plain HTTP on a LAN during testing, also add
`android:usesCleartextTraffic="true"` on the `<application>` tag (remove for
production — use HTTPS).

## Connecting the app
1. Start the backend on your PC/VPS: `python main.py api`
2. In the app's login screen enter:
   - **Server URL**: `http://<your-pc-ip>:8000` (or your HTTPS domain)
   - **Access key**: the `API_SECRET_KEY` from `.env`
3. The app polls for signals every 30 s and raises a notification + voice
   alert for each new actionable signal.

## Signing for Play Store
Standard Flutter flow: create a keystore, reference it in
`android/app/build.gradle` (`signingConfigs`), then
`flutter build appbundle --release`.
