/**
 * Thin fetch wrapper for the PHP API. Requests are aborted after a timeout so
 * a stalled backend can never freeze the live view. The API origin comes from
 * config.js because the UI is hosted by a separate static server.
 */

const TIMEOUT_MS = 6000;
const CONFIG = window.FURNACE_CONFIG ?? {};
const API_BASE = String(CONFIG.apiBaseUrl ?? '').trim().replace(/\/+$/, '');
const endpoint = (path) => `${API_BASE}${path}`;

/** Human-readable API origin, e.g. for the offline banner. */
export const API_LABEL = API_BASE.replace(/^https?:\/\//, '') || window.location.host;

/**
 * Explain a failed request as far as the browser allows.
 *
 * A browser reports a dead host, a certificate error, a mixed-content block and
 * a missing CORS header with the same opaque "Failed to fetch". One cheap
 * `no-cors` request separates them: it returns an opaque response as soon as
 * the server answers, even when CORS hides the body, and it throws only when
 * the request never completed. That distinction is what the operator needs on
 * a phone, where there is no console at hand.
 *
 * @returns {Promise<'mixed-content'|'cors'|'timeout'|'unreachable'>}
 */
export async function probeApi(timeoutMs = 4000) {
  if (window.location.protocol === 'https:' && API_BASE.startsWith('http://')) {
    return 'mixed-content';
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    await fetch(endpoint('/api/health'), {
      mode: 'no-cors',
      cache: 'no-store',
      credentials: 'omit',
      signal: controller.signal,
    });

    return 'cors';
  } catch (error) {
    return error?.name === 'AbortError' ? 'timeout' : 'unreachable';
  } finally {
    clearTimeout(timer);
  }
}

export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function request(path, { method = 'GET', body, timeout = TIMEOUT_MS } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);

  try {
    const response = await fetch(path, {
      method,
      headers: body === undefined
        ? { Accept: 'application/json' }
        : { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: 'no-store',
      credentials: 'omit',
      signal: controller.signal,
    });

    let payload = null;

    try {
      payload = await response.json();
    } catch {
      payload = null;
    }

    if (!response.ok) {
      throw new ApiError(payload?.error ?? `Request failed (${response.status})`, response.status);
    }

    return payload;
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }

    if (error?.name === 'AbortError') {
      throw new ApiError('The server did not respond in time');
    }

    throw new ApiError('Backend unreachable');
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  live({ since = 0, run = null } = {}) {
    const params = new URLSearchParams();

    if (since > 0) {
      params.set('since', String(since));
    }

    if (run) {
      params.set('run', String(run));
    }

    const query = params.toString();
    return request(endpoint(`/api/live${query ? `?${query}` : ''}`));
  },

  history({ window = 600, maxPoints = 1400, run = null } = {}) {
    const params = new URLSearchParams({
      window: String(window),
      max_points: String(maxPoints),
    });

    if (run) {
      params.set('run', String(run));
    }

    return request(endpoint(`/api/history?${params}`));
  },

  reset(label = '') {
    return request(endpoint('/api/runs/reset'), {
      method: 'POST',
      body: { label },
      timeout: 8000,
    });
  },

  /** Absolute first/last recorded sample, used by the range pickers. */
  bounds() {
    return request(endpoint('/api/bounds'));
  },

  /**
   * Min/max/average per furnace for an absolute range. Omitting the range
   * analyses everything the server has stored.
   */
  summary({ fromMs = null, toMs = null } = {}) {
    const params = new URLSearchParams();

    if (fromMs !== null) {
      params.set('from_ms', String(Math.round(fromMs)));
    }

    if (toMs !== null) {
      params.set('to_ms', String(Math.round(toMs)));
    }

    const query = params.toString();
    return request(endpoint(`/api/summary${query ? `?${query}` : ''}`), { timeout: 20000 });
  },

  /**
   * Download a CSV for the range. Returns the blob and the filename the API
   * chose, so the browser save dialog gets a meaningful name.
   */
  async exportData({ fromMs, toMs, resolution = 'auto' }) {
    const params = new URLSearchParams({
      from_ms: String(Math.round(fromMs)),
      to_ms: String(Math.round(toMs)),
      resolution,
    });
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 90000);

    try {
      const response = await fetch(endpoint(`/api/export?${params}`), {
        headers: { Accept: 'text/csv' },
        cache: 'no-store',
        credentials: 'omit',
        signal: controller.signal,
      });

      if (!response.ok) {
        let message = `Export failed (${response.status})`;

        try {
          const payload = await response.json();
          message = payload?.error ?? message;
        } catch {
          // Not JSON: keep the status message.
        }

        throw new ApiError(message, response.status);
      }

      const disposition = response.headers.get('Content-Disposition') ?? '';
      const match = /filename="?([^";]+)"?/i.exec(disposition);

      return {
        blob: await response.blob(),
        filename: match?.[1] ?? 'furnace-export.csv',
      };
    } catch (error) {
      if (error instanceof ApiError) {
        throw error;
      }

      if (error?.name === 'AbortError') {
        throw new ApiError('The export took too long; try a shorter range');
      }

      throw new ApiError('Backend unreachable');
    } finally {
      clearTimeout(timer);
    }
  },
};
