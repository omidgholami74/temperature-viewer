# MQTT dashboard

A dependency-free live view of the broker: four furnace cards, a continuous
chart, device fleet table and a transport log. It talks MQTT over WebSocket with
the bundled `mqtt.min.js` (mqtt.js 5, Apache-2.0) and renders the chart on a
plain canvas — no chart library, no build step.

```
MQTT/broker ──ws://…:9001──▶ mqtt.min.js ──▶ app.js ──▶ cards / canvas / table
```

## Run it

```bash
# from the repository root
docker compose -f MQTT/docker-compose.yml up -d broker dashboard
# open http://localhost:8080
```

The page is static; any web server works (`python3 -m http.server 8080` from
this folder). It must be served over HTTP(S), not `file://`, because the
WebSocket origin matters.

## Configure

Edit [`assets/config.js`](assets/config.js) on the deployed copy:

| Key | Meaning |
| --- | --- |
| `url` | Broker WebSocket URL (`ws://host:9001/mqtt`, `wss://…:9002/mqtt`) |
| `site` | Only topics for this site are shown; `*` accepts every site |
| `username` / `password` | Read-only broker account (`dashboard`) |
| `topics` | Subscriptions; the defaults cover samples, state and bridge status |
| `windowSeconds` | Visible chart span (600 = 10 minutes) |
| `pointsPerChannel` | Ring-buffer size per channel (7200 ≈ 12 minutes at 10 Hz) |

Nothing is written to the broker: the dashboard is a subscriber only, and the
ACL example grants it `topic read furnace/#`.

## Reading the screen

| Element | Meaning |
| --- | --- |
| Connection chip | Broker socket state: connected, reconnecting, offline |
| Live rate | Frames per second received in the last five seconds |
| Stored by bridge | `samples` and `forwarded` from the retained status topic |
| Dropped / duplicate | Bridge counters; a non-zero *dropped* means the API was down long enough to overflow the queue |
| Card chip | `live` (frame < 2.5 s old), `held` (holding the last good reading), `offline` |
| Chart | Continuous by design: a faulted channel holds its last value instead of breaking the line |

## Design system

[`styleguide.html`](styleguide.html) is the living reference: colour roles,
type scale, spacing, components and state rules, all rendered from the same
`tokens.css` / `app.css` pair. See [`../docs/design-system.md`](../docs/design-system.md)
for the written rules and [`../docs/images/design-board.svg`](../docs/images/design-board.svg)
for a one-page summary.

## Accessibility

* Canvas has `role="img"` and a description; values are also in the cards and
  the legend, so no reading exists only in the drawing.
* Every interactive element is a real button, select or row with a visible focus
  ring from `--accent`.
* `prefers-reduced-motion` disables the pulse and transitions.
* Contrast pairs are AA at the smallest sizes (see the design-system doc).

## Files

| File | Purpose |
| --- | --- |
| `index.html` | Dashboard shell |
| `styleguide.html` | Living design-system reference |
| `assets/config.js` | Broker URL, site, account, subscriptions |
| `assets/js/app.js` | MQTT handling, state, cards, canvas chart |
| `assets/js/mqtt.min.js` | mqtt.js 5 browser bundle (vendored) |
| `assets/css/tokens.css` | Design tokens, dark and light |
| `assets/css/app.css` | Component layer |
