import assert from 'node:assert/strict';
import test from 'node:test';

import {
  PORT_FILTERS,
  describeSerialSupport,
  matchesFilter,
  pickPort,
  portKey,
  requestOptions,
} from '../serial-web/assets/js/ports.js';

const CH343 = { usbVendorId: 0x1a86, usbProductId: 0x55d3 };
const PICO = { usbVendorId: 0x2e8a, usbProductId: 0x000a };
const VIRTUAL_COM = { usbVendorId: undefined, usbProductId: undefined };

test('the picker is unfiltered, so a CH343 bridge can be selected', () => {
  // A vendor filter here is the bug: Chrome hides every other device and the
  // ESP32 port is never offered, while a desktop app still opens it.
  assert.deepEqual(PORT_FILTERS, []);
  assert.deepEqual(requestOptions(), {});
  assert.equal(matchesFilter(CH343), true);
  assert.equal(matchesFilter({ getInfo: () => CH343 }), true);
  assert.equal(matchesFilter(PICO), true);
  assert.equal(matchesFilter(VIRTUAL_COM), true);
});

test('a narrowed filter list still hides devices and accepts the listed one', () => {
  const original = PORT_FILTERS.length;
  PORT_FILTERS.push({ usbVendorId: 0x1a86 });

  try {
    // Empty list -> no options; otherwise the filter is handed to Chrome.
    assert.deepEqual(requestOptions(), { filters: [{ usbVendorId: 0x1a86 }] });
    assert.equal(matchesFilter(CH343), true);
    assert.equal(matchesFilter(PICO), false);
    // The browser hides ports without USB metadata under a filter, so does this.
    assert.equal(matchesFilter(VIRTUAL_COM), false);
  } finally {
    PORT_FILTERS.length = original;
  }

  assert.deepEqual(requestOptions(), {});
});

test('a reload reopens the remembered board, not the first granted port', () => {
  const esp32 = { getInfo: () => CH343 };
  const pico = { getInfo: () => PICO };

  assert.equal(portKey(esp32), '6790:21971');
  assert.equal(portKey({ getInfo: () => VIRTUAL_COM }), '0:0');

  // Without a memory the browser order wins; with one, the operator's pick does.
  assert.equal(pickPort([pico, esp32], null), pico);
  assert.equal(pickPort([pico, esp32], portKey(esp32)), esp32);
  assert.equal(pickPort([], portKey(esp32)), null);
  assert.equal(pickPort(undefined, '1:2'), null);
  // A remembered port that is no longer granted falls back instead of failing.
  assert.equal(pickPort([pico], '9:9'), pico);
});

test('missing Web Serial is explained: insecure page or wrong browser', () => {
  const secure = { isSecureContext: true };
  const insecure = { isSecureContext: false };

  assert.equal(describeSerialSupport({ serial: {} }, secure), null);
  assert.match(describeSerialSupport({}, insecure), /secure page/);
  assert.match(describeSerialSupport({}, secure), /Chrome or Edge/);
  // A LAN address over http: the advice must be about the origin, not the browser.
  assert.doesNotMatch(describeSerialSupport({}, insecure), /Chrome or Edge/);
});
