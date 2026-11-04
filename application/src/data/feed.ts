import {useEffect, useRef, useState} from 'react';

import {CHANNELS} from './channels';
import {formatClock, round1} from '../utils/format';

/**
 * Live temperature feed.
 *
 * Everything on screen comes from the controller: there is no simulated series.
 *
 * `GET /api/live?since=<id>` returns only rows newer than the cursor, so each
 * 10 Hz burst is plotted frame by frame instead of one point per poll. Row times
 * are run-relative (`t_ms`), which is turned back into wall-clock time with
 * `run.started_at_ms`; a long silence between bursts then shows up as a gap in
 * the curve instead of a restart.
 *
 * The controller answers over plain HTTP because its TLS certificate is issued
 * for `*.web-hosting.com`, so Android rejects `https://` for this host. The
 * manifest pins cleartext permission to this single domain.
 */
export const API_BASE = 'http://apitemp.futurefounderco.com';
export const API_URL = `${API_BASE}/api/live`;

const POLL_MS = 1000;
const RETRY_MS = 3000;
const REQUEST_TIMEOUT_MS = 6000;
/** A burst of frames can be followed by a pause; one slow reply is not an outage. */
const FAILURE_TOLERANCE = 3;
const MAX_POINTS = 1500;
/** Fill the 15 minute chart from stored rows on launch. */
const SEED_WINDOW_S = 900;
const SEED_POINTS = 400;
/** Older than this and the board itself has stopped sending. */
const STALE_MS = 15000;

export type Point = {t: number; v: number};

export type ChannelReading = {
  id: number;
  name: string;
  color: string;
  temp: number | null;
  fault: number;
  delta: number;
  history: Point[];
  updatedAt: number;
};

export type FeedSource = 'connecting' | 'live' | 'offline';

export type TemperatureFeed = {
  source: FeedSource;
  channels: ChannelReading[];
  /** Time of the newest stored frame (server clock). */
  lastUpdate: number;
  sampleCount: number;
  startedAt: number | null;
  /** Age of the newest frame, in milliseconds, measured on the server clock. */
  staleMs: number;
  /** Persian sentence for the footer, or null when everything is healthy. */
  note: string | null;
  /** Raw failure description, kept for diagnostics. */
  error: string | null;
};

type Frame = {
  id?: number;
  t_ms?: number;
  received_at_ms?: number;
  temps?: (number | null)[];
  faults?: number[];
};

type RunInfo = {
  started_at_ms?: number;
  sample_count?: number;
  ended_at_ms?: number | null;
  running?: boolean;
} | null;

type LivePayload = {
  server_time_ms?: number;
  run?: RunInfo;
  latest?: Frame | null;
  samples?: Frame[];
  last_id?: number;
  resync?: boolean;
};

type HistoryPayload = {
  run?: RunInfo;
  points?: {t?: number; v?: (number | null)[]}[];
};

const emptyFeed = (): TemperatureFeed => ({
  source: 'connecting',
  channels: CHANNELS.map(channel => ({
    id: channel.id,
    name: channel.name,
    color: channel.color,
    temp: null,
    fault: 0,
    delta: 0,
    history: [],
    updatedAt: 0,
  })),
  lastUpdate: 0,
  sampleCount: 0,
  startedAt: null,
  staleMs: 0,
  note: null,
  error: null,
});

const deltaOf = (history: Point[]): number => {
  if (history.length < 2) {
    return 0;
  }
  const newest = history[history.length - 1];
  const older = history[Math.max(0, history.length - 7)];
  return round1(newest.v - older.v);
};

const startedAtOf = (...runs: (RunInfo | undefined)[]): number => {
  for (const run of runs) {
    const start = Number(run?.started_at_ms);
    if (Number.isFinite(start) && start > 0) {
      return start;
    }
  }
  return 0;
};

export function useTemperatureFeed(): TemperatureFeed {
  const [feed, setFeed] = useState<TemperatureFeed>(emptyFeed);
  const cursorRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    let failures = 0;
    let seeded = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const schedule = (delay: number) => {
      if (cancelled) {
        return;
      }
      timer = setTimeout(poll, delay);
    };

    const appendFrames = (frames: Frame[], start: number, at: number) => {
      setFeed(previous => {
        const channels = previous.channels.map((channel, index) => {
          const additions: Point[] = [];

          frames.forEach(frame => {
            const value = frame.temps?.[index];
            if (typeof value !== 'number' || !Number.isFinite(value)) {
              return;
            }
            const t = start + (frame.t_ms ?? 0);
            if (t <= 0) {
              return;
            }
            additions.push({t, v: round1(value)});
          });

          if (additions.length === 0) {
            return {...channel, updatedAt: at};
          }

          const history = channel.history.concat(additions).slice(-MAX_POINTS);
          const last = frames[frames.length - 1];
          const lastValue = last?.temps?.[index];

          return {
            ...channel,
            temp:
              typeof lastValue === 'number' && Number.isFinite(lastValue)
                ? round1(lastValue)
                : history[history.length - 1].v,
            fault: typeof last?.faults?.[index] === 'number' ? last.faults[index]! : 0,
            history,
            delta: deltaOf(history),
            updatedAt: at,
          };
        });

        return {...previous, channels};
      });
    };

    const seedHistory = async (run: RunInfo) => {
      const url = `${API_BASE}/api/history?window=${SEED_WINDOW_S}&max_points=${SEED_POINTS}`;
      const response = await fetch(url, {headers: {Accept: 'application/json'}});

      if (!response.ok) {
        return;
      }

      const payload = (await response.json()) as HistoryPayload;
      const points = Array.isArray(payload.points) ? payload.points : [];
      const start = startedAtOf(payload.run, run);

      if (points.length === 0 || start === 0) {
        return;
      }

      const history: Point[][] = CHANNELS.map(() => []);

      points.forEach(point => {
        const t = start + (point.t ?? 0);
        const values = point.v;
        if (t <= 0 || !Array.isArray(values)) {
          return;
        }
        values.forEach((value, index) => {
          if (typeof value === 'number' && Number.isFinite(value)) {
            history[index].push({t, v: round1(value)});
          }
        });
      });

      setFeed(previous => {
        const channels = previous.channels.map((channel, index) => {
          const rows = history[index];
          if (rows.length === 0) {
            return channel;
          }
          const merged = rows
            .concat(channel.history)
            .sort((left, right) => left.t - right.t)
            .slice(-MAX_POINTS);

          return {
            ...channel,
            history: merged,
            temp: merged[merged.length - 1].v,
            delta: deltaOf(merged),
          };
        });

        return {...previous, channels};
      });
    };

    const poll = async () => {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

      try {
        const url =
          cursorRef.current > 0 ? `${API_URL}?since=${cursorRef.current}` : API_URL;
        const response = await fetch(url, {
          signal: controller.signal,
          headers: {Accept: 'application/json', 'Cache-Control': 'no-cache'},
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const payload = (await response.json()) as LivePayload;
        const run = payload.run ?? null;
        const start = startedAtOf(run);
        const serverTime = Number(payload.server_time_ms) || Date.now();

        const frames = (Array.isArray(payload.samples) ? payload.samples : [])
          .concat(payload.latest ? [payload.latest] : [])
          .filter(frame => Array.isArray(frame.temps) && typeof frame.id === 'number')
          .sort((left, right) => (left.id ?? 0) - (right.id ?? 0));

        const fresh: Frame[] = [];
        frames.forEach(frame => {
          const id = frame.id ?? 0;
          if (id > cursorRef.current) {
            fresh.push(frame);
            cursorRef.current = id;
          }
        });

        if (cancelled) {
          return;
        }

        const newest = frames[frames.length - 1];
        const newestAbs = start > 0 && newest ? start + (newest.t_ms ?? 0) : 0;
        const staleMs = newestAbs > 0 ? Math.max(0, serverTime - newestAbs) : 0;

        failures = 0;

        if (fresh.length > 0 && start > 0) {
          appendFrames(fresh, start, newestAbs || serverTime);
        }

        setFeed(previous => {
          const sampleCount = Number(run?.sample_count);

          return {
            ...previous,
            source: 'live',
            lastUpdate: newestAbs || previous.lastUpdate,
            sampleCount: Number.isFinite(sampleCount)
              ? sampleCount
              : previous.sampleCount,
            startedAt: start > 0 ? start : previous.startedAt,
            staleMs,
            note:
              staleMs > STALE_MS && newestAbs > 0
                ? `دستگاه از ${formatClock(newestAbs)} داده‌ای نفرستاده — آخرین مقدار نمایش داده می‌شود`
                : null,
            error: null,
          };
        });

        // Seed the chart once from stored rows; the cursor is already
        // advanced by `latest`, so it cannot gate this.
        if (!seeded) {
          seeded = true;
          if (start > 0) {
            await seedHistory(run);
          }
        }

        schedule(POLL_MS);
      } catch (error) {
        if (!cancelled) {
          const message =
            error instanceof Error ? error.message : 'خطای نامشخص در ارتباط';
          failures += 1;

          if (failures >= FAILURE_TOLERANCE) {
            setFeed(previous => ({
              ...previous,
              source: 'offline',
              note: 'ارتباط با سرور پایش دما قطع است — تلاش مجدد خودکار',
              error: message,
            }));
          }

          schedule(RETRY_MS);
        }
      } finally {
        clearTimeout(timeout);
      }
    };

    poll();

    return () => {
      cancelled = true;
      if (timer) {
        clearTimeout(timer);
      }
    };
  }, []);

  return feed;
}
