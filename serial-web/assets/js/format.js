/**
 * Number and duration formatting. The timer deliberately has no 24 hour
 * ceiling: hours keep growing and the digit group simply gets wider.
 */

const pad2 = (value) => String(value).padStart(2, '0');

const countFormatter = new Intl.NumberFormat('en-US');
const dateTimeFormatter = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

/** Split milliseconds into unbounded hours, minutes, seconds and tenths. */
export function splitDuration(milliseconds) {
  const totalTenths = Math.max(0, Math.floor(Number(milliseconds) / 100));

  return {
    hours: Math.floor(totalTenths / 36000),
    minutes: Math.floor(totalTenths / 600) % 60,
    seconds: Math.floor(totalTenths / 10) % 60,
    tenths: totalTenths % 10,
  };
}

/**
 * Digits for the eight-character counter: HH:MM:SS:T.
 * Hours are padded to two digits and then allowed to grow past 99.
 */
export function formatDigits(milliseconds) {
  const { hours, minutes, seconds, tenths } = splitDuration(milliseconds);
  const hoursText = String(hours).padStart(2, '0');

  return {
    hours: hoursText,
    minutes: pad2(minutes),
    seconds: pad2(seconds),
    tenths: String(tenths),
    key: `${hoursText}${pad2(minutes)}${pad2(seconds)}${tenths}`,
  };
}

/** Masked clock used by chart tooltips: HH:MM:SS.T */
export function formatTimestamp(milliseconds) {
  const { hours, minutes, seconds, tenths } = splitDuration(milliseconds);
  return `${String(hours).padStart(2, '0')}:${pad2(minutes)}:${pad2(seconds)}.${tenths}`;
}

/** Human readable equivalent, e.g. "1 day, 4 hours, 12 minutes, 3.4 seconds". */
export function formatHuman(milliseconds) {
  const { hours, minutes, seconds, tenths } = splitDuration(milliseconds);
  const days = Math.floor(hours / 24);
  const dayHours = hours % 24;
  const parts = [];

  if (days > 0) {
    parts.push(plural(days, 'day'));
  }

  if (days > 0 || dayHours > 0) {
    parts.push(plural(dayHours, 'hour'));
  }

  if (hours > 0 || minutes > 0) {
    parts.push(plural(minutes, 'minute'));
  }

  parts.push(`${(seconds + tenths / 10).toFixed(1)} seconds`);

  return parts.join(', ');
}

function plural(value, word) {
  return `${value} ${word}${value === 1 ? '' : 's'}`;
}

/** Compact axis tick that adapts to the visible time span. */
export function formatAxisTick(milliseconds, spanMs) {
  const totalSeconds = Math.max(0, Math.round(Number(milliseconds) / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor(totalSeconds / 60) % 60;
  const seconds = totalSeconds % 60;
  const days = Math.floor(hours / 24);
  const dayPrefix = days > 0 ? `${days}d ` : '';
  const clock = `${pad2(hours % 24)}:${pad2(minutes)}`;

  if (spanMs <= 10 * 60 * 1000) {
    if (days > 0) {
      return `${dayPrefix}${clock}:${pad2(seconds)}`;
    }

    return hours > 0 ? `${hours}:${pad2(minutes)}:${pad2(seconds)}` : `${pad2(minutes)}:${pad2(seconds)}`;
  }

  if (spanMs <= 6 * 3600 * 1000) {
    return days > 0 ? `${dayPrefix}${clock}` : `${hours}:${pad2(minutes)}:${pad2(seconds)}`;
  }

  if (spanMs <= 48 * 3600 * 1000) {
    return days > 0 ? `${dayPrefix}${clock}` : `${hours}:${pad2(minutes)}`;
  }

  return `${days}d ${pad2(hours % 24)}:${pad2(minutes)}`;
}

export function formatTemp(value) {
  const numeric = Number(value);

  if (value === null || value === undefined || !Number.isFinite(numeric)) {
    return '--.-';
  }

  return numeric.toFixed(1);
}

export function formatDelta(value) {
  if (!Number.isFinite(value)) {
    return '—';
  }

  const rounded = Math.abs(value) < 0.05 ? 0 : value;
  const sign = rounded > 0 ? '+' : rounded < 0 ? '−' : '±';

  return `${sign}${Math.abs(rounded).toFixed(1)} °C`;
}

export function formatAge(milliseconds) {
  if (milliseconds === null || milliseconds === undefined || !Number.isFinite(milliseconds)) {
    return '—';
  }

  if (milliseconds < 1000) {
    return `${Math.max(0, Math.round(milliseconds))} ms ago`;
  }

  if (milliseconds < 60000) {
    return `${(milliseconds / 1000).toFixed(1)} s ago`;
  }

  if (milliseconds < 3600000) {
    return `${Math.floor(milliseconds / 60000)} min ago`;
  }

  return `${Math.floor(milliseconds / 3600000)} h ago`;
}

export function formatCount(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? countFormatter.format(numeric) : '0';
}

export function formatDateTime(milliseconds) {
  const numeric = Number(milliseconds);

  if (!Number.isFinite(numeric) || numeric <= 0) {
    return '—';
  }

  return dateTimeFormatter.format(new Date(numeric));
}
