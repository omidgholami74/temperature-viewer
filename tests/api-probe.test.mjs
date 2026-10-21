import assert from 'node:assert/strict';
import test from 'node:test';

/**
 * `api.js` reads `window` at import time, so every case installs its own page
 * and imports a fresh module instance through a unique query string.
 */
let generation = 0;

function loadApi({ pageUrl, apiBaseUrl, fetchImpl }) {
  globalThis.window = {
    location: {
      protocol: new URL(pageUrl).protocol,
      hostname: new URL(pageUrl).hostname,
      host: new URL(pageUrl).host,
    },
    FURNACE_CONFIG: { apiBaseUrl },
  };
  globalThis.fetch = fetchImpl;

  generation += 1;
  return import(`../ui/assets/js/api.js?case=${generation}`);
}

function opaqueFetch() {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return { type: 'opaque', status: 0 };
  };
  fetchImpl.calls = calls;
  return fetchImpl;
}

test('a mixed-content setup is reported without touching the network', async () => {
  const fetchImpl = opaqueFetch();
  const { probeApi } = await loadApi({
    pageUrl: 'https://temp.futurefounderco.com/',
    apiBaseUrl: 'http://apitemp.futurefounderco.com',
    fetchImpl,
  });

  assert.equal(await probeApi(), 'mixed-content');
  assert.equal(fetchImpl.calls.length, 0);
});

test('an opaque answer means the host replied and CORS hid it', async () => {
  const fetchImpl = opaqueFetch();
  const { probeApi, API_LABEL } = await loadApi({
    pageUrl: 'http://temp.futurefounderco.com/',
    apiBaseUrl: 'http://apitemp.futurefounderco.com',
    fetchImpl,
  });

  assert.equal(API_LABEL, 'apitemp.futurefounderco.com');
  assert.equal(await probeApi(), 'cors');
  assert.equal(fetchImpl.calls.length, 1);
  assert.equal(fetchImpl.calls[0].url, 'http://apitemp.futurefounderco.com/api/health');
  assert.equal(fetchImpl.calls[0].options.mode, 'no-cors');
});

test('a rejected probe is a host that cannot be reached', async () => {
  const { probeApi } = await loadApi({
    pageUrl: 'http://temp.futurefounderco.com/',
    apiBaseUrl: 'https://apitemp.futurefounderco.com',
    fetchImpl: async () => {
      throw new TypeError('Failed to fetch');
    },
  });

  assert.equal(await probeApi(), 'unreachable');
});

test('a probe that never answers times out instead of hanging the banner', async () => {
  const { probeApi } = await loadApi({
    pageUrl: 'http://temp.futurefounderco.com/',
    apiBaseUrl: 'http://apitemp.futurefounderco.com',
    fetchImpl: (url, options) => new Promise((resolve, reject) => {
      options.signal.addEventListener('abort', () => {
        const error = new Error('aborted');
        error.name = 'AbortError';
        reject(error);
      }, { once: true });
    }),
  });

  assert.equal(await probeApi(10), 'timeout');
});
