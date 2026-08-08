# Build the Vanshika Android APK locally

The mobile app is configured as a branded Android WebView wrapper for:

`https://www.vanshikamakeoveracademy.com/`

## Requirements

- Node.js 18+
- pnpm
- Android Studio
- Android SDK Platform 35
- Android SDK Build-Tools
- Android SDK Platform-Tools
- Java 17 or newer

Set `ANDROID_HOME` to your Android SDK directory and make sure these are on
`PATH`:

- `$ANDROID_HOME/platform-tools`
- `$ANDROID_HOME/emulator`
- `$ANDROID_HOME/cmdline-tools/latest/bin`

## Build a test/install APK

From the project root:

```bash
pnpm install
cd artifacts/mobile
pnpm exec expo prebuild --platform android
pnpm exec expo run:android --variant release
```

The generated APK is normally located at:

```text
artifacts/mobile/android/app/build/outputs/apk/release/app-release.apk
```

You can install it on a connected device with:

```bash
adb install -r android/app/build/outputs/apk/release/app-release.apk
```

## Play Store release

For Google Play distribution, configure a private release keystore and
Android signing credentials before building. Do not commit the keystore,
passwords, or signing credentials to this project.

## App identity

- App name: Vanshika Makeover Academy
- Android package: `com.vanshikamakeoveracademy.app`
- Version: `1.0.0`
- Version code: `1`

The app opens the live HTTPS website, so the installed phone needs internet
access. It cannot use a development `localhost` URL.