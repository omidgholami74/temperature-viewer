export const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));

export const round1 = (value: number) => Math.round(value * 10) / 10;

/** One decimal, or a dash when a channel is faulted and has no reading. */
export const formatTemp = (value: number | null | undefined): string => {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return '--';
  }
  return value.toFixed(1);
};

export const formatDelta = (value: number): string => {
  const rounded = round1(value);
  if (rounded > 0.05) {
    return `+${rounded.toFixed(1)}`;
  }
  if (rounded < -0.05) {
    return rounded.toFixed(1);
  }
  return '0.0';
};

const pad = (value: number) => String(value).padStart(2, '0');

export const formatClock = (ms: number): string => {
  const date = new Date(ms);
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
};

export const formatClockShort = (ms: number): string => {
  const date = new Date(ms);
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

export const formatAge = (ms: number): string => {
  const seconds = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (seconds < 5) {
    return 'همین حالا';
  }
  if (seconds < 60) {
    return `${seconds} ثانیه پیش`;
  }
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) {
    return `${minutes} دقیقه پیش`;
  }
  const hours = Math.round(minutes / 60);
  return `${hours} ساعت پیش`;
};

export type Trend = 'heating' | 'cooling' | 'holding';

export const trendOf = (delta: number): Trend => {
  if (delta >= 0.4) {
    return 'heating';
  }
  if (delta <= -0.4) {
    return 'cooling';
  }
  return 'holding';
};

export const trendLabel: Record<Trend, string> = {
  heating: 'در حال گرمایش',
  cooling: 'در حال سرمایش',
  holding: 'دمای پایدار',
};

export const trendColor: Record<Trend, string> = {
  heating: '#FF7A45',
  cooling: '#37D4FF',
  holding: '#3EE0A5',
};
