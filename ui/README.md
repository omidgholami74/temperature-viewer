# Web app

A static, dependency-free dashboard: plain HTML, CSS custom properties and ES
modules. There is no build step and no npm install; Chart.js is vendored so the
page works on an isolated plant network.

The PHP front controller in `backend/` serves these files and the API from the
same origin.

## Screens

1. Four furnace cards: name, colour, live temperature, 10-second change and a
   heating / cooling / holding state. A faulted channel shows `--.-` and the
   decoded fault reason.
2. Run timer: eight digit positions `HH:MM:SS:T`, with days/hours/minutes
   spelled out below and sample count, rate and last-sample age underneath.
   "New run" starts a fresh timer after a confirmation.
3. Chart: four linear series on one canvas, a custom legend that doubles as a
   series toggle, window presets (1 min, 10 min, 1 h, 6 h, 24 h, all) and an
   empty state while waiting for the first sample.

## Files

```
index.html                  markup, four static furnace cards, timer, chart
assets/css/tokens.css       design tokens, reset, base typography
assets/css/app.css          layout and components
assets/js/main.js           state, DOM updates, event wiring, rAF timer
assets/js/feed.js           chained /api/live polling with backoff
assets/js/api.js            fetch wrapper with timeouts
assets/js/chart-panel.js    Chart.js setup, buffers, window and visibility
assets/js/format.js         duration, axis, temperature and delta formatting
assets/vendor/chart.umd.min.js  Chart.js 4.5.1 (MIT, see CHARTJS-LICENSE.md)
assets/img/favicon.svg      favicon
```

## Data flow

- `LiveFeed` requests `/api/live?since=<last_id>` every **250 ms** (2 s while
  the tab is hidden), chained so requests never overlap. Failures back off to
  4 s. A request that finishes after "New run" was pressed is discarded
  (`generation` guard) so the cursor cannot jump back to the old run.
- Every sample in the response is appended to the chart buffer; the cards,
  legend, counter and status use the newest frame.
- History is fetched on load, on window change, when another tab starts a new
  run, when the tab becomes visible, and every 20 s for windows longer than two
  minutes. Requests are cancelled logically with a token so stale answers are
  ignored.
- The counter is repainted with `requestAnimationFrame`, but only digits that
  changed are touched. It is interpolated from the server's `elapsed_ms`, so it
  stays smooth between polls and never depends on the browser's midnight.
- The chart window is anchored to the newest recorded sample, so a stopped Pico
  still shows its last data instead of an empty chart.

## Design system

`tokens.css` is the single source of truth:

- surfaces (`--bg-0` … `--surface-3`), lines, three text levels;
- semantic colours (`--accent`, `--warn`, `--danger`, `--info`);
- channel identity `--ch-1` … `--ch-4` reused by cards, legend and chart;
- spacing scale (4 px base), radii, elevation, motion tokens;
- type scale with a fluid counter size `clamp(42px, 12vw, 112px)`.

Components only use these tokens. The layout is a single column of panels with
a 4/2/1 column furnace grid at 1180 px and 560 px breakpoints. Focus rings,
reduced-motion support, a skip link, `aria-pressed` on toggles and a live
connection status keep it usable with a keyboard and a screen reader.

## Performance choices

- Chart.js runs with `animation: false`, `parsing: false`, `normalized: true`
  and zero-radius points.
- Each series keeps its own point buffer that is mutated in place; trimming
  uses one splice instead of repeated `shift()`.
- Long windows (6 h, 24 h, all) are averaged into buckets **inside SQLite**, so
  the browser receives at most 1,400 points instead of hundreds of thousands.
- Live values update at poll rate (4 Hz), not at the ingest rate, and the chart
  renders once per poll.
- Tabular numerals and fixed-height containers prevent layout shift while
  numbers change at 10 Hz.
- Fonts are system fonts; no network requests, no layout-blocking assets.

## Running

Use the backend, which serves both the API and this folder:

```bash
php backend/bin/install.php
php -S 0.0.0.0:8080 -t backend backend/index.php
```

Then open <http://localhost:8080>.

## Tests

```bash
node --test tests/format.test.mjs
```

The tests cover the eight-digit counter, unbounded hours, the human-readable
equivalent, axis labels across short and multi-day runs, and delta/temperature
formatting.

## Browser support

Current Chrome, Edge, Firefox and Safari. The design uses CSS `color-mix()` and
ES modules; Internet Explorer is not supported.
