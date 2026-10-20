/**
 * Thin fetch wrapper for the PHP API. Requests are aborted after a timeout so
 * a stalled backend can never freeze the live view.
 */

const TIMEOUT_MS = 6000;

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
      credentials: 'same-origin',
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
    return request(`/api/live${query ? `?${query}` : ''}`);
  },

  history({ window = 600, maxPoints = 1400, run = null } = {}) {
    const params = new URLSearchParams({
      window: String(window),
      max_points: String(maxPoints),
    });

    if (run) {
      params.set('run', String(run));
    }

    return request(`/api/history?${params}`);
  },

  reset(label = '') {
    return request('/api/runs/reset', {
      method: 'POST',
      body: { label },
      timeout: 8000,
    });
  },
};
