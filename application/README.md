# اتوماسیون هوشمند رازی — Razi Smart Automation (Android)

A React Native (bare) Android app for the four-channel furnace controller.
It shows live thermocouple temperatures, a per-channel trend and an animated
multi-series chart, in Persian, on a dark glass-style surface.

| | |
| --- | --- |
| App name (launcher) | اتوماسیون هوشمند رازی |
| Company | بنیاد علوم کاربردی رازی (Razi Applied Science Foundation) |
| Package | `com.raziautomation` |
| Version | 1.2.0 (versionCode 3) |
| Min / target SDK | 24 / 34 |
| JS engine | Hermes, old architecture |

## Features

- **Animated splash screen** with the foundation mark, a rotating dashed halo,
  the app and company name, and a progress bar. The native launch theme is the
  same navy, so there is no white flash before React Native paints.
- **Sign-in panel** before the dashboard: the foundation mark, RTL username and
  password fields with a show/hide toggle, inline Persian errors, a shake on a
  failed attempt and a spinner while it checks. The signed-in user and role sit
  in the footer next to a sign-out button.
- **Live dashboard**: a 270° arc gauge for the selected furnace, heating /
  cooling / holding state, 10-second delta, and min / average / max for the
  selected window.
- **Four channel cards** (کوره ۱–۴) with live value, trend and sparkline. Tap a
  card to make it the gauge channel.
- **Animated chart** drawn with `react-native-svg`: smooth curves, gradient
  areas, grid, per-series legend toggles, 1 / 5 / 15 minute windows and an
  animated stroke reveal when the window changes.
- **Data feed** reads `http://apitemp.futurefounderco.com/api/live?since=<id>`
  every second. The cursor means every stored frame is plotted — a 10 Hz burst
  lands as a dense segment instead of one point per poll — and each row is placed
  at its real run time, so a pause between bursts shows as a gap instead of the
  chart restarting. On launch the last 15 minutes are seeded from `/api/history`.
  There is no simulated data: a channel the controller reports as `null` shows
  `--`, and if the board stops sending for 15 s the footer names the time of the
  last frame. A dropped link keeps the last curve behind an **offline** pill and
  retries every 3 s.
- **RTL-friendly Persian copy**, fixed-width digits so values never shift the
  layout, and a soft blue glow background instead of flat black.

> **Why plain HTTP?** The controller's TLS certificate is issued for
> `*.web-hosting.com`, so `https://apitemp.futurefounderco.com` fails hostname
> verification on Android. The app therefore uses the HTTP endpoint and
> `android/app/src/main/res/xml/network_security_config.xml` permits cleartext
> for that one host only (everything else stays HTTPS-only). Once the server has
> a valid certificate, switch `API_BASE` in `src/data/feed.ts` to `https://` and
> drop that config.

## Screens

```
SplashScreen ──2.5 s──▶ LoginScreen ──sign in──▶ DashboardScreen
                          ├── brand header + live/offline pill
                          ├── hero gauge card (selected furnace + stats)
                          ├── 4 channel cards (tap to select)
                          ├── chart card (window chips + legend)
                          └── source / last-update footer
```

## Sign-in

The controller API only protects ingestion with an API key, so the dashboard is
gated locally in `src/data/auth.ts`:

| Username | Password | Role |
| --- | --- | --- |
| `admin` | `razi1400` | مدیر |
| `operator` | `kore1400` | اپراتور |

Replace that account list with the operator accounts, or point `signIn()` at a
real endpoint once the backend grows one. The session lives in memory, so
closing the app asks for the password again.

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
roughly 47 MB, version 1.2.0). Install it with:

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
src/data/channels.ts        channel names, colours, gauge scales
src/data/feed.ts            API polling + useTemperatureFeed()
src/data/auth.ts            local accounts + signIn()
src/screens/LoginScreen     animated sign-in panel
src/components/BrandLogo    the company mark (header, splash, login)
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

`logo.png` at the repository root of this folder is the artwork supplied by the
company. The build assets are derived from it with `sharp`, never redrawn:

- the white paper is knocked out to transparency (`alpha = 255 - min(r,g,b)`),
  the mark is trimmed and tinted;
- `assets/brand/mark-light.png` (light blue, for the dark UI) and
  `mark-plain.png` (brand blue) feed `BrandLogo`, the splash and the launcher
  icons;
- `icon-1024.png` / `icon-round-1024.png` place the mark on the navy gradient,
  and the mipmap PNGs plus `splash_logo.png` are resized from those.

Replace `logo.png` with a new export and re-run the generator to refresh every
icon at once.

## Checks

```bash
npx tsc --noEmit     # typecheck
npm test             # jest smoke test for App
```
