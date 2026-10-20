import { api } from './api.js';

const POLL_MS = 250;
const HIDDEN_POLL_MS = 2000;
const MAX_BACKOFF_MS = 4000;

/**
 * Polls /api/live and exposes only the samples the UI has not seen yet.
 * The loop is chained (never overlapping) and backs off when the API fails.
 */
export class LiveFeed {
  #timer = null;
  #running = false;
  #busy = false;
  #since = 0;
  #runId = null;
  #delay = POLL_MS;
  #generation = 0;

  constructor({ onData, onError }) {
    this.onData = onData;
    this.onError = onError;
  }

  start() {
    if (this.#running) {
      return;
    }

    this.#running = true;
    this.#tick();
  }

  stop() {
    this.#running = false;
    clearTimeout(this.#timer);
  }

  /** Follow a run and restart the sample cursor. */
  selectRun(runId) {
    this.#runId = runId ?? null;
    this.#since = 0;
    // Invalidate a request that is already in flight for the old run.
    this.#generation += 1;
  }

  resetCursor() {
    this.#since = 0;
    this.#generation += 1;
  }

  /** Poll immediately, e.g. after the tab becomes visible again. */
  wake() {
    if (!this.#running) {
      return;
    }

    clearTimeout(this.#timer);
    this.#delay = POLL_MS;
    this.#tick();
  }

  async #tick() {
    if (!this.#running || this.#busy) {
      return;
    }

    this.#busy = true;
    const generation = this.#generation;

    try {
      const payload = await api.live({ since: this.#since, run: this.#runId });

      // A new run was selected while this request was in flight; its answer
      // belongs to the previous run and must not move the cursor back.
      if (generation !== this.#generation) {
        return;
      }

      this.#delay = POLL_MS;

      if (payload.run) {
        this.#runId = payload.run.id;
      }

      if (payload.resync) {
        // The client fell too far behind; history will fill the gap.
        this.#since = 0;
      } else if (typeof payload.last_id === 'number') {
        this.#since = payload.last_id;
      }

      this.onData(payload);
    } catch (error) {
      this.#delay = Math.min(Math.round(this.#delay * 1.8), MAX_BACKOFF_MS);
      this.onError(error);
    } finally {
      this.#busy = false;

      if (this.#running) {
        clearTimeout(this.#timer);
        this.#timer = setTimeout(
          () => this.#tick(),
          document.hidden ? HIDDEN_POLL_MS : this.#delay,
        );
      }
    }
  }
}
