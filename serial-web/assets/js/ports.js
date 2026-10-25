/**
 * Which serial devices the picker may offer, and how a saved permission is
 * matched after a reload.
 *
 * Web Serial hides every port whose USB ids are not in `filters`. A list that
 * names one vendor therefore hides every other board: the dialog opens without
 * a selectable device and the port cannot be chosen at all. The CH343 (WCH,
 * vendor `0x1a86`) bridge used on ESP32 boards is exactly that case, while a
 * desktop application such as Serial Studio still opens it, because it does
 * not go through the browser picker.
 *
 * An empty list shows every serial port the system reports. Narrow it only
 * when the machine really has other serial devices worth hiding:
 *
 *   export const PORT_FILTERS = [{ usbVendorId: 0x1a86 }]; // CH340/CH343
 *
 * Known vendor ids: 0x2e8a Raspberry Pi, 0x1a86 WCH (CH340/CH343/CH9102),
 * 0x303a Espressif, 0x10c4 Silicon Labs CP210x, 0x0403 FTDI, 0x2341 Arduino.
 *
 * @type {{ usbVendorId?: number, usbProductId?: number }[]}
 */
export const PORT_FILTERS = [];

/**
 * Does a port (or its `getInfo()` payload) satisfy the current filter list?
 * Ports with no USB metadata, such as a virtual COM port, only pass an empty
 * filter list — that is the browser's rule too, so the picker and the
 * reconnect path agree.
 */
export function matchesFilter(candidate) {
  const info = typeof candidate?.getInfo === 'function' ? candidate.getInfo() : candidate ?? {};

  if (!PORT_FILTERS.length) {
    return true;
  }

  return PORT_FILTERS.some(
    (filter) =>
      (filter.usbVendorId === undefined || filter.usbVendorId === info.usbVendorId) &&
      (filter.usbProductId === undefined || filter.usbProductId === info.usbProductId),
  );
}

/** Stable identity of a port, for remembering the one the operator chose. */
export function portKey(candidate) {
  const info = typeof candidate?.getInfo === 'function' ? candidate.getInfo() : candidate ?? {};
  return `${info.usbVendorId ?? 0}:${info.usbProductId ?? 0}`;
}

/**
 * Which already-granted port a reload should reopen. Without a vendor filter
 * every granted device passes, so the remembered one wins instead of whichever
 * the browser happens to list first.
 *
 * @param {object[]} ports result of `navigator.serial.getPorts()`
 * @param {string|null} preferred key returned by `portKey` last time
 */
export function pickPort(ports, preferred = null) {
  const granted = Array.from(ports ?? []).filter(matchesFilter);

  if (!granted.length) {
    return null;
  }

  if (preferred) {
    const remembered = granted.find((candidate) => portKey(candidate) === preferred);

    if (remembered) {
      return remembered;
    }
  }

  return granted[0];
}

/**
 * Argument for `navigator.serial.requestPort()`. An empty filter list is
 * passed as no options at all, so Chrome shows every available port instead of
 * an empty dialog.
 */
export function requestOptions() {
  return PORT_FILTERS.length ? { filters: PORT_FILTERS } : {};
}

/**
 * Why the serial API is missing, or `null` when it is usable. The two causes
 * need different advice: an insecure page (a LAN address over plain http) can
 * never expose `navigator.serial`, no matter which browser is used.
 *
 * @returns {string|null}
 */
export function describeSerialSupport(nav = globalThis.navigator, scope = globalThis) {
  if (nav && 'serial' in nav) {
    return null;
  }

  if (scope?.isSecureContext === false) {
    return 'Web Serial needs a secure page: open this dashboard on http://localhost or https://, not on a LAN address over http';
  }

  return 'Web Serial is not available in this browser. Use Chrome or Edge on a desktop';
}
