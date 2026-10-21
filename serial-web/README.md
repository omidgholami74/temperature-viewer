# Serial web monitor

The same dashboard as [`ui/`](../ui) — identical timer, cards, chart and design
tokens, with the timer at the top and everything on one screen that never
scrolls — fed directly from the USB serial port with the browser's Web Serial
API. No backend, no collector, no database.

![Serial web monitor](../artifacts/serial-web-dashboard.png)

## What the Pico must send

In `module/config.py`:

```python
SERIAL_FORMAT = "csv"   # default in config.example.py
SERIAL_ECHO = True
```

The firmware prints a header once, then one line per sample (10 per second):

```
ms,furnace1,furnace2,furnace3,furnace4
12300,781.25,660.00,940.00,545.75
12400,781.25,660.00,940.00,-1000.00
```

`-1000.00` means that channel reported a fault. The page shows `--.-`, marks
the card red and draws a gap in the chart. JSON lines
(`{"u":…,"t":[…],"f":[…]}`) are accepted too, so `SERIAL_FORMAT = "json"`
still works.

The flash log on the Pico stays JSON, so `collector/` can upload it later.

## Run it

1. Close Thonny, Serial Studio and `collector.py` — a serial port can only be
   open in one program at a time.
2. Serve this folder (Web Serial needs `localhost` or HTTPS):

   ```bash
   cd serial-web
   python -m http.server 8080
   ```

3. Open <http://localhost:8080> in **Chrome or Edge**.
4. Click **Connect port** and pick the Pico (`USB Serial Device (COM5)`).

The picker is a browser security requirement: a page may not open a port by
name without the user choosing it once. Press the button again to disconnect.

### Where is the port set?

`COM5` never appears in the code. The browser does not let a page name a port,
so the operator picks it in the dialog; Chrome remembers the permission for
that site afterwards. Only two settings live in
[`assets/js/serial.js`](assets/js/serial.js):

```js
const PORT_FILTERS = [{ usbVendorId: 0x2e8a }]; // Raspberry Pi boards only
const BAUD_RATE = 115200;                       // must match the firmware
const AUTO_CONNECT = true;                      // reopen the allowed port
```

`PORT_FILTERS` narrows the picker to Raspberry Pi USB devices; clear it
(`const PORT_FILTERS = [];`) if your board does not show up. To check what the
browser sees, open the console on the page and run
`await navigator.serial.getPorts()`.

With `AUTO_CONNECT = true`, a page reload reopens the port the operator already
allowed, without the picker. Set it to `false` to choose the port by hand every
time. Unplugging the cable is detected and the page returns to “Ready”.

## Serial Studio

Point Serial Studio at the same port, **Quick Plot**, 115200 baud. The first
line is text, so it becomes the channel labels and the numeric rows after it
are plotted. `-1000` shows as a sharp dip, which makes a faulted probe obvious.
To fix the channel names and units instead of the header row, `-1000` can be
mapped in a Project File.

## How it works

`index.html` is the `ui/` layout. `assets/js/serial.js` replaces the API client:

1. `navigator.serial.requestPort()` opens the port, `readable` is decoded as
   UTF-8 text and split on newlines.
2. Each line becomes a frame (`u` uptime + four temperatures, `null` for a
   fault). A Pico reboot (uptime going backwards) starts a new session.
3. Frames are appended to the same `TemperatureChart` class used by `ui/`,
   using uptime differences, so the 0.1 s grid stays exact.
4. Cards, legend, timer, window buttons and toasts run the same formatting
   helpers (`format.js`) as the API dashboard.

Styles, `format.js` and `chart-panel.js` are copied from `ui/`, so the pages
look identical. Change them in `ui/` first, then copy the files over.

For a quick check without hardware, open the browser console and run
`furnaceSerial.applyFrame(furnaceSerial.parseLine('100,700,650,940,545'))`;
the cards and chart update exactly as they would from the port.

## Files

| File                            | Purpose                                    |
| ------------------------------- | ------------------------------------------ |
| `index.html`                    | Dashboard markup (same as `ui/index.html`) |
| `assets/js/serial.js`           | Web Serial reader and UI controller        |
| `assets/js/chart-panel.js`      | Chart, copied from `ui/`                   |
| `assets/js/format.js`           | Number/timer formatting, copied from `ui/` |
| `assets/css/`                   | Design tokens and layout, copied from `ui/`|
| `assets/vendor/chart.umd.min.js`| Chart.js 4, copied from `ui/`              |

Nothing is written to disk; closing the tab ends the session. For a permanent
history, use `collector/` and the API dashboard instead.

## Troubleshooting

| Symptom                              | Check                                                     |
| ------------------------------------ | --------------------------------------------------------- |
| `Web Serial is not available…`       | Chrome or Edge, and open the page from `http://localhost`  |
| `Could not open the port…`           | Close Thonny/Serial Studio/collector; replug the Pico      |
| Connects but stays on “Waiting”      | `SERIAL_FORMAT = "csv"`, `SERIAL_ECHO = True`, 115200 baud |
| Every card shows `--.-`              | Run `module/check.py` on the Pico to see the sensor fault  |
| Chart is empty on a fresh page       | It fills when the first numeric line arrives               |
