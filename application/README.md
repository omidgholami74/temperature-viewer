# اتوماسیون هوشمند رازی — Razi Smart Automation (Android)

A React Native (bare) Android app for the four-channel furnace controller.
It shows live thermocouple temperatures, a per-channel trend and an animated
multi-series chart, in Persian, on a dark glass-style surface.

| | |
| --- | --- |
| App name (launcher) | اتوماسیون هوشمند رازی |
| Company | بنیاد علوم کاربردی رازی (Razi Applied Science Foundation) |
| Package | `com.raziautomation` |
| Version | 1.0.0 (versionCode 1) |
| Min / target SDK | 24 / 34 |
| JS engine | Hermes, old architecture |

## Features

- **Animated splash screen** with the foundation mark, a rotating dashed halo,
  the app and company name, and a progress bar. The native launch theme is the
  same navy, so there is no white flash before React Native paints.
- **Live dashboard**: a 270° arc gauge for the selected furnace, heating /
  cooling / holding state, 10-second delta, and min / average / max for the
  selected window.
- **Four channel cards** (کوره ۱–۴) with live value, trend and sparkline. Tap a
  card to make it the gauge channel.
- **Animated chart** drawn with `react-native-svg`: smooth curves, gradient
  areas, grid, per-series legend toggles, 1 / 5 / 15 minute windows and an
  animated stroke reveal when the window changes.
- **Data feed** polls `https://apitemp.futurefounderco.com/api/live` every 1.5 s.
  When the server is unreachable the app switches to a realistic local
  simulation and labels it **دمو**, retrying the API every 30 s. The footer
  always states which source is on screen, so a simulated curve is never
  mistaken for a real firing.
- **RTL-friendly Persian copy**, fixed-width digits so values never shift the
  layout, and a soft blue glow background instead of flat black.

> The production API currently serves a certificate that does not match
> `apitemp.futurefounderco.com`, so Android rejects it and the app stays in demo
> mode until the certificate is fixed (or the base URL is changed in
> `src/data/feed.ts`).

## Screens

```
SplashScreen  ──2.5 s──▶  DashboardScreen
                          ├── brand header + live/demo pill
                          ├── hero gauge card (selected furnace + stats)
                          ├── 4 channel cards (tap to select)
                          ├── chart card (window chips + legend)
                          └── source / last-update footer
```

## Run it

```bash
cd application
npm install          # dependencies are not committed
npm start            # Metro
npx react-native run-android
```

For a physical phone, `adb reverse tcp:8081 tcp:8081` or a LAN Metro URL works.

## Build the APK

The Android SDK is not vendored in this repository. Point Gradle at your JDK 17
and Android SDK, then build the signed release:

```bash
cd application/android
export JAVA_HOME=/path/to/jdk-17
export ANDROID_HOME=/path/to/android-sdk        # platform 35 + build-tools 35.0.0
./gradlew assembleRelease
```

The APK lands in
`android/app/build/outputs/apk/release/app-release.apk` (arm64-v8a + armeabi-v7a,
roughly 30 MB). Install it with:

```bash
adb install -r android/app/build/outputs/apk/release/app-release.apk
```

## Signing

`android/app/razi-release.keystore` is an **internal distribution key**
(alias `razi`, password `razi-automation`, RSA 2048, valid 30 years) so this
build is instal­lable and updatable. It is committed only so the app can be
rebuilt with the same signature. Before publishing on Google Play, generate a
private keystore and replace `signingConfigs.release` in
`android/app/build.gradle`; never publish this key for a store release.

## Project structure

```
App.tsx                     splash → dashboard switch, status bar
src/theme.ts                palette, radii, spacing, shadows
src/data/channels.ts        channel names, colours, gauge scale, demo baselines
src/data/feed.ts            API polling + demo generator + useTemperatureFeed()
src/components/BrandLogo    vector peacock mark (header, splash)
src/components/SplashScreen animated launch screen
src/components/Gauge        animated 270° arc gauge
src/components/Sparkline    tiny trend line for the channel cards
src/components/TemperatureChart  animated multi-series area chart
src/components/ChannelCard  furnace tile
src/screens/DashboardScreen composition, selection, windows, stats
assets/brand/               source SVGs and a 1024 px preview of the icon
android/app/src/main/res/   launcher icons and the native launch background
```

## Brand assets

`assets/brand/logo-razi.svg` is the vector mark; `app-icon.svg` and
`app-icon-round.svg` are the launcher icon sources. The PNG mipmaps,
`icon-1024.png` and the splash bitmap were rasterised from those SVGs with
`sharp`, so the artwork stays editable:

```bash
npx sharp-cli -i assets/brand/app-icon.svg -o android/app/src/main/res/mipmap-xxxhdpi/ic_launcher.png resize 192 192
```

## Checks

```bash
npx tsc --noEmit     # typecheck
npm test             # jest smoke test for App
```
