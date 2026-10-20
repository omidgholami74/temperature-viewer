import { formatAxisTick, formatTemp, formatTimestamp } from './format.js';

const MIN_WINDOW_MS = 60_000;
const DEFAULT_WINDOW_MS = 600_000;

/**
 * Four line series on a linear time axis.
 *
 * Points are kept in per-channel buffers that are mutated in place, so a
 * 0.1 s ingest does not allocate thousands of objects on every repaint.
 */
export class TemperatureChart {
  constructor(canvas, channels) {
    if (!window.Chart) {
      throw new Error('Chart.js is not available');
    }

    const rootStyle = getComputedStyle(document.documentElement);
    const color = (name, fallback) => rootStyle.getPropertyValue(name).trim() || fallback;

    this.Chart = window.Chart;
    this.channels = channels;
    this.windowMs = DEFAULT_WINDOW_MS;
    this.spanMs = DEFAULT_WINDOW_MS;
    this.buffers = channels.map(() => []);
    this.colors = channels.map((channel, index) => color(`--ch-${index + 1}`, channel.fallback));

    const textMuted = color('--text-3', '#6e839a');
    const textSecondary = color('--text-2', '#a4b7ca');
    const grid = color('--line', 'rgba(143,172,202,.13)');
    const border = color('--line-strong', 'rgba(143,172,202,.24)');

    this.Chart.defaults.font.family = color('--font-sans', 'system-ui, sans-serif');
    this.Chart.defaults.font.size = 11;
    this.Chart.defaults.color = textMuted;

    this.chart = new this.Chart(canvas, {
      type: 'line',
      data: {
        datasets: channels.map((channel, index) => ({
          label: channel.name,
          data: this.buffers[index],
          borderColor: this.colors[index],
          backgroundColor: this.colors[index],
          borderWidth: 2,
          borderCapStyle: 'round',
          borderJoinStyle: 'round',
          pointRadius: 0,
          pointHoverRadius: 3,
          pointHitRadius: 10,
          tension: 0,
          spanGaps: false,
          fill: false,
        })),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        parsing: false,
        normalized: true,
        resizeDelay: 120,
        interaction: { mode: 'index', intersect: false, axis: 'x' },
        layout: { padding: { top: 6, right: 4, left: 0, bottom: 0 } },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: 'rgba(7, 13, 21, 0.96)',
            borderColor: border,
            borderWidth: 1,
            cornerRadius: 8,
            padding: 10,
            caretSize: 4,
            boxWidth: 8,
            boxHeight: 8,
            usePointStyle: true,
            titleColor: textSecondary,
            bodyColor: '#e9f2fb',
            titleFont: { size: 11, weight: '600' },
            bodyFont: { size: 11 },
            callbacks: {
              title: (items) => (items.length ? formatTimestamp(items[0].parsed.x) : ''),
              label: (context) => `${context.dataset.label}  ${formatTemp(context.parsed.y)} °C`,
            },
            filter: (item) => item.parsed.y !== null,
          },
        },
        scales: {
          x: {
            type: 'linear',
            min: 0,
            max: DEFAULT_WINDOW_MS,
            grid: { color: grid, drawTicks: false, lineWidth: 1 },
            border: { color: grid },
            ticks: {
              color: textMuted,
              maxTicksLimit: 7,
              autoSkip: true,
              maxRotation: 0,
              padding: 8,
              font: { size: 10 },
              callback: (value) => formatAxisTick(Number(value), this.spanMs),
            },
          },
          y: {
            min: undefined,
            max: undefined,
            grace: '8%',
            grid: { color: grid, drawTicks: false, lineWidth: 1 },
            border: { display: false },
            ticks: {
              color: textMuted,
              padding: 8,
              font: { size: 10 },
              callback: (value) => `${Math.round(Number(value))}`,
            },
            title: {
              display: true,
              text: '°C',
              color: textMuted,
              font: { size: 10, weight: '600' },
            },
          },
        },
      },
    });

  }

  get pointCount() {
    return this.buffers[0]?.length ?? 0;
  }

  /** Set the visible time window in milliseconds. */
  setWindow(windowMs, { render = true } = {}) {
    this.windowMs = Math.max(1, Number(windowMs) || DEFAULT_WINDOW_MS);
    this.#trim();

    if (render) {
      this.render();
    }
  }

  /** Replace every series with bucketed history points. */
  replace(points) {
    this.buffers.forEach((buffer, channel) => {
      buffer.length = 0;

      for (const point of points) {
        buffer.push({ x: point.t, y: point.v?.[channel] ?? null });
      }
    });

    this.#trim();
    this.render();
  }

  /** Append raw samples in order, ignoring duplicates. */
  appendSamples(samples) {
    for (const sample of samples) {
      const last = this.buffers[0][this.buffers[0].length - 1];

      if (last && sample.t_ms <= last.x) {
        continue;
      }

      this.buffers.forEach((buffer, channel) => {
        buffer.push({ x: sample.t_ms, y: sample.temps?.[channel] ?? null });
      });
    }

    this.#trim();
    this.render();
  }

  clear() {
    this.buffers.forEach((buffer) => {
      buffer.length = 0;
    });
    this.render();
  }

  setVisible(channel, visible) {
    this.chart.setDatasetVisibility(channel, visible);
    this.chart.update('none');
  }

  /** Last known value at or before a point in time, used for the 10 s delta. */
  sampleAt(channel, tMs) {
    const buffer = this.buffers[channel];

    if (!buffer || buffer.length === 0) {
      return null;
    }

    let low = 0;
    let high = buffer.length - 1;
    let found = null;

    while (low <= high) {
      const middle = (low + high) >> 1;

      if (buffer[middle].x <= tMs) {
        found = buffer[middle];
        low = middle + 1;
      } else {
        high = middle - 1;
      }
    }

    return found ? found.y : null;
  }

  render() {
    const latest = this.buffers[0].length
      ? this.buffers[0][this.buffers[0].length - 1].x
      : 0;

    this.spanMs = Math.max(this.windowMs, MIN_WINDOW_MS);

    const max = Math.max(latest, this.spanMs);

    // Look the scale options up on every render: Chart.js resolves and
    // replaces its internal option objects during the first update.
    const scale = this.chart.options.scales.x;
    scale.max = max;
    scale.min = max - this.spanMs;

    this.chart.update('none');
  }

  #trim() {
    const latest = this.buffers[0].length
      ? this.buffers[0][this.buffers[0].length - 1].x
      : 0;
    const cutoff = latest - this.windowMs;

    if (latest <= 0) {
      return;
    }

    for (const buffer of this.buffers) {
      let drop = 0;

      while (drop < buffer.length && buffer[drop].x < cutoff) {
        drop += 1;
      }

      if (drop > 0) {
        buffer.splice(0, drop);
      }
    }
  }
}
