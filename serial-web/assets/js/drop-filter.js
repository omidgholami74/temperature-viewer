/** Suppress isolated downward spikes without hiding sustained real drops. */
export class TemperatureDropFilter {
  constructor({ enabled = true, threshold = 60, baseline = [] } = {}) {
    this.enabled = Boolean(enabled);
    this.threshold = this.#threshold(threshold);
    this.previous = Array.from({ length: 4 }, (_, index) => this.#number(baseline[index]));
    this.pending = [false, false, false, false];
  }

  #threshold(value) {
    const number = Number(value);
    return Number.isFinite(number) ? Math.min(95, Math.max(5, number)) : 60;
  }

  #number(value) {
    const number = Number(value);
    return value !== null && value !== undefined && Number.isFinite(number) ? number : null;
  }

  configure({ enabled = this.enabled, threshold = this.threshold } = {}) {
    this.enabled = Boolean(enabled);
    this.threshold = this.#threshold(threshold);
    this.pending.fill(false);
  }

  reset(baseline = []) {
    this.previous = Array.from({ length: 4 }, (_, index) => this.#number(baseline[index]));
    this.pending.fill(false);
  }

  process(values = []) {
    const pendingAtStart = [...this.pending];
    return Array.from({ length: 4 }, (_, index) => {
      const current = this.#number(values[index]);
      const previous = this.previous[index];

      if (current === null) {
        // Sensor gaps invalidate the percentage baseline; the next valid frame
        // seeds it instead of being mistaken for a spike against stale data.
        this.previous[index] = null;
        this.pending[index] = false;
        return null;
      }

      if (!this.enabled || previous === null || previous <= 0 || current >= previous) {
        this.previous[index] = current;
        this.pending[index] = false;
        return current;
      }

      const dropPercent = ((previous - current) / previous) * 100;
      if (dropPercent < this.threshold) {
        this.previous[index] = current;
        this.pending[index] = false;
        return current;
      }

      if (pendingAtStart[index]) {
        // A second consecutive low sample confirms a true temperature change.
        this.previous[index] = current;
        this.pending[index] = false;
        return current;
      }

      this.pending[index] = true;
      return previous;
    });
  }
}
