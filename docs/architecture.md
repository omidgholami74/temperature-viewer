# Architecture — furnace temperature monitoring

Design document for the temperature monitoring system: firmware, transport,
storage, analytics and dashboard.

Status: **design**. The sections marked *as-built* describe code that exists in
this repository today; everything marked *target* is proposed and not yet
implemented. See [Roadmap](#roadmap) for what is next.

---

## 1. Purpose and scope

Watch N thermocouple channels across one or more furnaces, record every sample
with a trustworthy timestamp, show the live trend, and raise an alert when the
process leaves its allowed band.

In scope:

- sensor acquisition and fault detection on the device,
- a durable, gap-aware path from the device to storage,
- time-series history with retention,
- live and historical views for an operator,
- deterministic analytics and alerting.

Out of scope, by design:

- **closed-loop control.** This is a monitoring system. It must never be the
  only over-temperature protection; a hardware limit controller or thermal
  cut-out stays in the loop, independently wired.
- multi-tenant user management (single operator group, one deployment),
- cloud-scale ingestion (see [Scaling](#8-scaling-path)).

## 2. Current state (as-built)

```
  ESP32-S3 (Wi-Fi)                        API                      Browser
 ┌──────────────────────┐   POST    ┌──────────────────┐   GET   ┌──────────┐
 │ 4 × MAX31855 @ 10 Hz │ ────────▶ │ /api/ingest      │ ──────▶ │  ui/     │
 │ buffer → JSON batch   │ X-Api-Key │ PHP + SQLite     │  JSON   │ dashboard│
 └──────────────────────┘           │ runs + readings  │         └──────────┘
                                    └──────────────────┘
```

| Concern | As built | Where |
| --- | --- | --- |
| Acquisition | 4 channels, 10 Hz, MAX31855 or MAX31865 | `esp32-s3/`, `module/` |
| Device clock | NTP, absolute `at` per sample, uptime `u` fallback | `esp32-s3/common/wifi.py`, `ingest.py` |
| Fault reporting | per-channel bitmask `1` open, `2` short-GND, `4` short-VCC, `8` read error | firmware, `Api::validateSamples()` |
| Offline buffering | in-RAM queue, bounded, oldest dropped and counted | `esp32-s3/common/ingest.py` |
| Transport | HTTP `POST /api/ingest`, batches ≤ 600 samples, keep-alive | `backend/index.php` |
| Storage | SQLite: `readings` raw + `readings_1s` rollup + `runs` | `backend/schema.sql` |
| Retention | raw 48 h, 1 s rollup 180 d, cron maintenance | `backend/bin/maintenance.php` |
| History API | server-side bucketing, raw → 1 s → 1 min | `GET /api/history` |
| Live view | client polls `/api/live?since=<id>` for new rows | `ui/assets/js/feed.js` |
| Analytics | min/max/avg per channel per range, with the time of each extreme | `GET /api/summary` |
| Export | CSV, Iran local time, selectable channels and resolution | `GET /api/export` |
| Dashboard | static HTML/CSS/ES modules, Chart.js, dark + light, PWA | `ui/` |

This already covers the "phase 1: monitoring" goal of the original plan: the
board reads the sensors, joins Wi-Fi, pushes to the server, the server stores
history and the browser draws it live.

### Known weaknesses in the current design

1. **One shared ingest key.** `FURNACE_INGEST_KEY` is a single secret for every
   device. Revoking one board means rotating the key on all of them, and a
   leaked key lets anyone write samples anywhere.
2. **No device identity in storage.** Samples carry no `device_id`; the data
   model assumes exactly one board and four channels per run. A second board
   would silently merge into the same run.
3. **No gap or duplicate accounting.** A batch that never arrives leaves a
   shorter history with nothing recording *why*. There is no `sequence`
   continuity check on the server.
4. **Alerts are visual only.** No thresholds, no alert records, no history of
   when a channel left its band.
5. **Polling, not push.** At 1 Hz across a few browsers over shared hosting this
   is fine; at dozens of operators it is wasted work (see `§8`).

Weakness 1–3 are data-model problems and are addressed by
[database-schema.md](database-schema.md). Weakness 4 is the next feature.
Weakness 5 is deliberately deferred.

## 3. Target architecture (target)

```
┌──────────────── hardware ────────────────┐
│ ESP32-S3 + N × MAX31855 (K-type)          │
│  • shared SPI, one CS per channel          │
│  • conversion ~70–100 ms  → 10 Hz ceiling  │
│  • NTP clock, monotonic sequence per boot  │
└───────────────┬───────────────────────────┘
                │  MQTT over TLS (optional)   or   HTTP POST /api/ingest
┌───────────────▼───────────────────────────┐
│ transport adapter                         │
│  • MQTT consumer (devices → validated)     │
│  • or the existing HTTP endpoint           │
└───────────────┬───────────────────────────┘
                │  one validated envelope, identical for both paths
┌───────────────▼───────────────────────────┐
│ backend modules (separable, no cycles)     │
│  ingest      validate + map device→sensor  │
│  storage     raw write + retention         │
│  analytics   rates, stability, comparisons │
│  alerts      rules → state machine → rows  │
│  realtime    push to connected browsers    │
│  api         read/export surface           │
└───────────────┬───────────────────────────┘
                │
┌───────────────▼───────────────────────────┐
│ PostgreSQL + TimescaleDB (target)          │
│  hypertables, continuous aggregates,       │
│  compression + retention policies          │
└───────────────┬───────────────────────────┘
                │
┌───────────────▼───────────────────────────┐
│ dashboard (static, token-driven design)    │
└───────────────────────────────────────────┘
```

### Module contracts

The rule that keeps this extensible: **each module only knows the module to its
right.** Ingest does not write SQL; analytics does not know about transports;
the dashboard only reads the API.

| Module | Input | Output | Must not |
| --- | --- | --- | --- |
| `ingest` | raw frame (HTTP body or MQTT message) | validated frames + device health | touch SQL or the UI |
| `storage` | validated frames | rows, buckets, ranges | parse transport payloads |
| `analytics` | queried ranges | metrics (min/max/avg, slope, stability) | write |
| `alerts` | metrics + rules | alert events | format for the UI |
| `realtime` | stored rows | fan-out to browsers | be required for correctness |
| `api` | HTTP | JSON / CSV | compute analytics inline |

Realtime is a delivery optimisation, not a source of truth: the browser must be
able to restore full state from the REST API alone after any disconnect.

## 4. Key decisions

| # | Decision | Choice | Why, and what it costs |
| --- | --- | --- | --- |
| D1 | Device transport | **Keep HTTP ingest as the supported path; add MQTT as an optional adapter** | One board, one writer, batches of 600 already work with keep-alive. MQTT buys per-device credentials, "device offline" events and push-fanout, at the cost of a broker process to run, monitor and secure. Add it when a second device appears, not before. The wire payload is reused unchanged, so this is additive. |
| D2 | Time-series store | **SQLite now, TimescaleDB when history outgrows one host** | 10 Hz × 4 channels = ~3.5 M rows/day raw, which SQLite handles at this size with the existing bucketed reads. TimescaleDB adds continuous aggregates, compression and retention as *policies* instead of cron scripts. Migrating before the box is the limit is premature; migrating after the schema is set is cheap (see `database-schema.md` §5). |
| D3 | Channel count | **Per-device channel count is data, not code** | The brief's three furnaces and this repo's four channels must both work. A device row carries its channel count; queries read `ch1..ch4` with the unused ones `NULL`. |
| D4 | Sample rate | **Sample at 10 Hz, transport in 1 s batches, keep 1 s forever** | The MAX31855 conversion time is 70–100 ms, so 10 Hz is the sensor's own ceiling and 1 s would throw away resolution that freezes of a thermocouple weld show. Batch on the wire (1 message/s), keep raw briefly, keep the 1 s average forever. |
| D5 | Analytics first version | **Deterministic metrics, no ML** | Threshold, rate of change, trend, stability and cross-furnace comparison are explainable in one sentence to an operator and need no training data. ML anomaly detection is phase 3: it needs labelled history that does not exist yet. |
| D6 | Dashboard stack | **Keep the static ES-module dashboard** | It already ships the design system, PWA install, themes and server-side bucketing. A React rewrite changes the build chain and adds a dependency tree without changing what the operator sees. Revisit only if the UI needs many stateful screens. |
| D7 | Live update | **Polling for now, WebSocket/SSE when push is measured to matter** | 1 Hz polling of a `no-store` JSON row is ~1 KB; five operators is trivial. Push becomes worth its reconnect logic when observers or devices grow. |
| D8 | Trust in time | **Keep both `at` (device) and `received_at_ms` (server)** | A device clock can be wrong after a failed NTP sync; the server clock can be behind a replayed buffer. Storing both makes the discrepancy auditable instead of invisible. |

## 5. Data flow and timing budget

Per device, steady state:

| Stage | Interval / layout | Note |
| --- | --- | --- |
| SPI read | 10 Hz | ~100 ms conversion, one channel per 25 ms slice |
| Batch flush | 1 s (10 frames) | under the 600-sample API cap |
| Ingest → rows | 10 rows / s | one transaction per batch |
| Browser poll | 1 s (`/api/live?since=`) | returns only new rows |
| Chart window | ≥ 10 min bucketed | server averages, so payload stays flat |
| Rollup | 1 min cron | raw → 1 s buckets, then retention |

A 10-minute chart is 600 points regardless of history length; that is the
property that keeps the page fast for months of data.

## 6. Failure modes

| Failure | Detected by | System behaviour | Operator sees |
| --- | --- | --- | --- |
| Thermocouple open / short | MAX31855 fault bits | `null` value + fault bit, never a fake number | `Sensor error` chip on that card |
| Wi-Fi loss | device reconnect loop | samples buffer in RAM, oldest dropped, drops counted | cards stop updating, status *Paused* |
| Server down | device HTTP error | queue kept, slow retry, no busy loop | dashboard *Backend offline* banner |
| Device reboot | new `boot_id`, sequence restarts | server treats sequence per boot, not global | new run/boot marker in history |
| Clock unsynced | `at` missing or far from server time | server anchors to receive time, flags skew | tooltip shows recorded vs received |
| Batch lost in transit | missing `seq` range | gap recorded as an event, not silently stitched | gap in the chart, note in events |
| Sensor stuck (flat line) | stability metric | *Unstable/Stuck* hint, not a hard fault | warning chip |
| Disk full | maintenance job error | ingest fails loudly (500), API health degrades | banner + `raw_rows` in `/api/health` |

Nothing silently repairs: a fault is stored as a fault, a gap as a gap.

## 7. Security model

- **Per-device credentials** (target): one MQTT user or one API token per board,
  scoped to that board's topics. Replaces the shared `FURNACE_INGEST_KEY`
  (keep the shared key working for single-board installs).
- **Transport TLS** whenever the server is not on the same isolated LAN;
  `TLS_VERIFY = False` in the firmware is a documented development shortcut,
  not a deployment setting.
- **Write scoping**: the device may only publish telemetry/status for itself.
  Commands are the only server→device path and are logged.
- **Browser access**: the dashboard is read-only by default; run resets and
  settings require a trusted origin (already enforced by
  `Api::requireTrustedOrigin`).
- **No secrets in the repo**: `module/config.py`, `esp32-s3/**/config.py` and
  `config.h` are ignored by Git for exactly this reason.

## 8. Scaling path

| Scale | Bottleneck | Change |
| --- | --- | --- |
| 1 device, 4 channels | none | current design |
| 2–10 devices | identity + credentials | add `devices`/`sensors`, per-device auth, MQTT adapter |
| 10–50 devices | ingest write rate, shared PHP host | dedicated host, TimescaleDB, batched async writes |
| 50+ devices | polling fan-out, single DB writer | WebSocket/SSE, partitioned writers, read replica |

The steps are ordered so that none of them requires rewriting the module to its
left: identity is data, transport is an adapter, storage is behind one
repository interface.

## 9. Observability

- `GET /api/health`: schema version, raw row count, whether an ingest key is
  required. Used by the dashboard banner and by uptime checks.
- Per-device state in `devices`: `last_seen`, `boot_id`, `seq_gap_count`,
  `dropped_samples`, `firmware_version`, `rssi`. "Is the board alive?" must be
  answerable from the database, not from a log file.
- `device_events` (target): connect, disconnect, reboot, config change, gap —
  an audit trail next to the measurements.
- Logs stay operational (one line per failure); data problems belong in tables.

## 10. Roadmap

**Phase 1 — monitoring (as-built).** Board → server → live chart, run timer,
analysis range, CSV export, retention. Acceptance: 24 h of 10 Hz data with no
lost sample while the network is stable.

**Phase 2 — identity, analytics, alerts.**
1. `devices` + `sensors` tables, `device_id` and `sensor_id` on every row.
2. Per-device credentials; keep the shared key for single-board installs.
3. Sequence-gap accounting and `device_events`.
4. Alert rules with hysteresis and cooldown; `alerts` rows with `firing` /
   `resolved` states.
5. Analytics endpoints: rate of change (°C/min), trend, stability, cross-furnace
   comparison.
6. Alert strip and analytics panel in the dashboard.

Acceptance: pull a thermocouple and see a `sensor_error` alert within 2 s; block
the network for 60 s and see exactly one gap event, no invented data, and the
buffer flush afterwards.

**Phase 3 — operations.** MQTT adapter with per-device topics, push updates,
scheduled reports (daily/monthly CSV + PDF), notification channels (Telegram,
email) behind a webhook, forecasting of time-to-target on a validated dataset.

Acceptance for MQTT: two boards publishing concurrently, each rejected when it
publishes into the other's subtree, and an offline board producing a retained
`status: offline` message within one keep-alive interval.

## 11. Open decisions (owner input)

These change implementation order, not the structure above. Recommended
defaults are in brackets.

1. **Server location** — LAN-only, cloud VPS, or both. [LAN host at the factory;
   cloud read-only mirror later. Monitoring must survive an internet outage.]
2. **Device count in 12 months** — one, a few, or dozens. [A few: build device
   identity now, MQTT adapter when the second board arrives.]
3. **Sample interval** — 1 s, 2 s, 5 s, or configurable per device. [10 Hz
   sampling with 1 s transport and 1 s retention; interval configurable per
   device later.]
4. **Analytics wanted** — band alerts, trend, ML anomaly detection, forecast,
   cross-furnace comparison, reports. [Band + rate-of-change + trend +
   stability + comparison now; ML and forecast in phase 3.]
5. **Host hardware** — Raspberry Pi / mini PC, VPS, Windows PC. [Raspberry Pi 5
   or a small x86 mini PC with a UPS; Windows only for development.]

## 12. Safety

Alarms, thresholds and analytics in this system are advisory. Independent
hardware protection (limit controller, thermal cut-out), correct grounding and
isolated thermocouples remain mandatory for any furnace that can injure someone
or destroy product.
