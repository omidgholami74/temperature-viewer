# Web app

A standalone static dashboard: plain HTML, CSS custom properties and ES
modules. There is no build step and no npm install; Chart.js is vendored so the
page works without a CDN.

This folder is deployed on its **own web server**, separate from the PHP API:

| Service | Production URL |
| ------- | -------------- |
| UI (this folder) | <https://temp.futurefounderco.com> |
| API | <https://apitemp.futurefounderco.com> |

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

## Configuration

`config.js` is loaded before the app and selects the API origin:

```js
const isLocalHost = ['localhost', '127.0.0.1', '::1'].includes(window.location.hostname);

window.FURNACE_CONFIG = {
  apiBaseUrl: isLocalHost
    ? 'http://localhost:8080'
    : 'https://apitemp.futurefounderco.com',
};
```

- Production is untouched: opening the site on `temp.futurefounderco.com`
  calls `https://apitemp.futurefounderco.com`.
- Local development with `python3 -m http.server 3000 --directory ui` calls
  `http://localhost:8080`.
- Set `apiBaseUrl: ''` when both the UI and the API are published under one
  reverse-proxy origin.
- There is no trailing slash and no `/api` suffix; the client appends paths.

The API must allow this origin through CORS. The backend default already
includes `https://temp.futurefounderco.com`, `http://localhost:3000` and
`http://127.0.0.1:3000`; override `FURNACE_ALLOWED_ORIGINS` on the API host if
you deploy the UI elsewhere.

## Files

```
index.html                  markup, four static furnace cards, timer, chart
config.js                   API origin for the current deployment
assets/css/tokens.css       design tokens, reset, base typography
assets/css/app.css          layout and components
assets/js/main.js           state, DOM updates, event wiring, rAF timer
assets/js/feed.js           chained /api/live polling with backoff
assets/js/api.js            fetch wrapper, API base URL and timeouts
assets/js/chart-panel.js    Chart.js setup, buffers, window and visibility
assets/js/format.js         duration, axis, temperature and delta formatting
assets/vendor/chart.umd.min.js  Chart.js 4.5.1 (MIT, see CHARTJS-LICENSE.md)
assets/img/favicon.svg      favicon
```

All asset paths are relative, so the site can also be hosted under a sub-path.

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
- Every API call is cross-origin, uses `credentials: 'omit'` and relies on the
  backend's CORS allow-list. No cookies or tokens are stored in the browser.

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

## Local development

```bash
# terminal 1 — API
php backend/bin/install.php
php -S 0.0.0.0:8080 -t backend backend/index.php

# terminal 2 — UI
python3 -m http.server 3000 --directory ui

# terminal 3 — simulated Pico
python3 module/simulator.py
```

The simulator needs no arguments; edit `API_URL`, `API_KEY` and the intervals
at the top of `module/simulator.py` if you change the API host.

Open <http://localhost:3000>. The local branch of `config.js` points at port
8080 and the backend already allows the localhost origins.

## Hosting

`ui/` is plain static content. Point `temp.futurefounderco.com` at this folder:

```nginx
server {
    listen 443 ssl;
    server_name temp.futurefounderco.com;
    root /srv/furnace/ui;

    location / {
        try_files $uri $uri/ =404;
    }

    location ~* \.(css|js)$ {
        add_header Cache-Control "public, max-age=300";
    }

    add_header Content-Security-Policy "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self' http://apitemp.futurefounderco.com https://apitemp.futurefounderco.com; base-uri 'none'; form-action 'none'" always;
}
```

Notes:

- Do **not** proxy `/api` from the UI host; the browser must reach the API
  origin directly so CORS and the correct API URL apply.
- `config.js` follows the page scheme (`https` page → `https` API, `http` page
  → `http` API), so the same file works while the hosts only serve http and
  after certificates are installed. An HTTPS page cannot call an HTTP API, and
  an HTTPS API needs a certificate that matches its hostname.
- The API only echoes CORS headers for allow-listed origins, and `http://` and
  `https://` are different origins. Both are in the backend default; keep both
  if you override `FURNACE_ALLOWED_ORIGINS`.
- If you add a `connect-src` policy, list every scheme/host the browser may
  call, or the fetch is blocked before it leaves the page.
- Bump or disable the `config.js` cache when you change the API origin.
- `chart.umd.min.js` is vendored; no CDN or external font request is made.

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
