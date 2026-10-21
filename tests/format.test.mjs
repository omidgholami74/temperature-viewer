import assert from 'node:assert/strict';
import test from 'node:test';

import { TemperatureDropFilter } from '../ui/assets/js/drop-filter.js';
import { TemperatureDropFilter as SerialTemperatureDropFilter } from '../serial-web/assets/js/drop-filter.js';
import {
  formatAxisTick,
  formatCount,
  formatDelta,
  formatDigits,
  formatHuman,
  formatShortDuration,
  formatStamp,
  formatTemp,
  splitDuration,
} from '../ui/assets/js/format.js';

test('splitDuration keeps tenths, seconds, minutes and hours apart', () => {
  assert.deepEqual(splitDuration(0), { hours: 0, minutes: 0, seconds: 0, tenths: 0 });
  assert.deepEqual(splitDuration(999), { hours: 0, minutes: 0, seconds: 0, tenths: 9 });
  assert.deepEqual(splitDuration(1234), { hours: 0, minutes: 0, seconds: 1, tenths: 2 });
  assert.deepEqual(splitDuration(3_723_400), { hours: 1, minutes: 2, seconds: 3, tenths: 4 });
});

test('the counter always has eight digit slots', () => {
  const digits = formatDigits(0);

  assert.equal(digits.hours, '00');
  assert.equal(digits.minutes, '00');
  assert.equal(digits.seconds, '00');
  assert.equal(digits.tenths, '0');
  // The tenths group renders a dim leading zero plus the live digit.
  assert.equal(`0${digits.tenths}`.length, 2);
});

test('hours keep counting past 24 and 99', () => {
  assert.equal(formatDigits(26 * 3_600_000).hours, '26');
  assert.equal(formatDigits(100 * 3_600_000).hours, '100');
  assert.equal(formatDigits((49 * 3600 + 61) * 1000).hours, '49');
  assert.equal(formatDigits((49 * 3600 + 61) * 1000).minutes, '01');
});

test('human equivalent names days when the run is longer than 24 h', () => {
  const elapsed = 26 * 3_600_000 + 8_300;

  assert.equal(formatHuman(elapsed), '1 day, 2 hours, 0 minutes, 8.3 seconds');
  assert.equal(formatHuman(4_200), '4.2 seconds');
  assert.equal(formatHuman(65_000), '1 minute, 5.0 seconds');
  assert.equal(formatHuman(2 * 86_400_000), '2 days, 0 hours, 0 minutes, 0.0 seconds');
});

test('axis ticks stay readable for short and multi-day runs', () => {
  assert.equal(formatAxisTick(0, 600_000), '00:00');
  assert.equal(formatAxisTick(3_600_000, 600_000), '1:00:00');
  assert.equal(formatAxisTick(93_600_000, 600_000), '1d 02:00:00');
  assert.equal(formatAxisTick(93_600_000, 86_400_000), '1d 02:00');
  assert.equal(formatAxisTick(259_200_000, 7 * 86_400_000), '3d 00:00');
});

test('short durations fit the stat row', () => {
  assert.equal(formatShortDuration(0), '0 s');
  assert.equal(formatShortDuration(4100), '4.1 s');
  assert.equal(formatShortDuration(65_000), '1 m 05 s');
  assert.equal(formatShortDuration(3 * 3_600_000 + 12 * 60_000), '3 h 12 m');
  assert.equal(formatShortDuration(50 * 3_600_000), '2 d 02 h');
  assert.equal(formatShortDuration(Number.NaN), '0 s');
});

test('range timestamps include the year and handle missing data', () => {
  assert.equal(formatStamp(0), '—');
  assert.equal(formatStamp(Number.NaN), '—');

  const formatted = formatStamp(new Date(2026, 9, 21, 4, 5).getTime());
  assert.match(formatted, /2026/);
  assert.match(formatted, /04:05/);
});

test('counts use thousands separators', () => {
  assert.equal(formatCount(93_917), '93,917');
  assert.equal(formatCount(null), '0');
  assert.equal(formatCount('12'), '12');
});

for (const [name, Filter] of [['online UI', TemperatureDropFilter], ['serial UI', SerialTemperatureDropFilter]]) {
  test(`${name} noise filter suppresses isolated configured drops and accepts sustained changes`, () => {
    const filter = new Filter({ enabled: true, threshold: 60, baseline: [100, 200, null, 50] });

    assert.deepEqual(filter.process([39, 80, 30, 40]), [100, 200, 30, 40]);
    assert.deepEqual(filter.process([101, 79, 31, 39]), [101, 79, 31, 39]);

    filter.reset([100, 200, 300, 400]);
    assert.deepEqual(filter.process([39, 200, 300, 400]), [100, 200, 300, 400]);
    assert.deepEqual(filter.process([38, 200, 300, 400]), [38, 200, 300, 400]);
  });

  test(`${name} noise filter supports disable, threshold change, and sensor gaps`, () => {
    const filter = new Filter({ enabled: true, threshold: 40, baseline: [100, 100, 100, 100] });
    assert.deepEqual(filter.process([60, null, 59, 100]), [100, null, 100, 100]);
    assert.deepEqual(filter.process([61, 59, 59, 100]), [61, 59, 59, 100]);
    filter.configure({ enabled: false, threshold: 60 });
    assert.deepEqual(filter.process([20, 100, 100, 100]), [20, 100, 100, 100]);
    filter.configure({ enabled: true, threshold: 80 });
    filter.reset([100, 100, 100, 100]);
    assert.deepEqual(filter.process([20, 100, 100, 100]), [100, 100, 100, 100]);
  });
}

test('temperature and delta formatting never renders NaN', () => {
  assert.equal(formatTemp(null), '--.-');
  assert.equal(formatTemp(Number.NaN), '--.-');
  assert.equal(formatTemp(783.66), '783.7');
  assert.equal(formatDelta(0), '±0.0 °C');
  assert.equal(formatDelta(-1.24), '−1.2 °C');
  assert.equal(formatDelta(Number.NaN), '—');
});
