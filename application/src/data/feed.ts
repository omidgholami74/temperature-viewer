import {useEffect, useRef, useState} from 'react';

import {CHANNELS} from './channels';
import {round1} from '../utils/format';

/**
 * Live temperature feed.
 *
 * Readings come from the controller API only — the app keeps no simulated
 * series. When the server cannot be reached the last values stay on screen and
 * the UI reports that the link is down while retrying in the background.
 *
 * The controller is served over plain HTTP: its TLS certificate is issued for
 * `*.web-hosting.com` and does not match `apitemp.futurefounderco.com`, so
 * Android refuses the `https://` URL for that host. The manifest therefore
 * carries a network security config that permits cleartext for this host only.
 */
export const API_BASE = 'http://apitemp.futurefounderco.com';
export const API_URL = `${API_BASE}/api/live`;

const POLL_MS = 1500;
const RETRY_MS = 4000;
const REQUEST_TIMEOUT_MS = 6000;
/**
 * The controller bursts samples and then goes quiet for several seconds, and a
 * single slow request is not a real outage, so the pill only turns to «قطع»
 * after this many failures in a row. Until then the last curve stays live.
 */
const FAILURE_TOLERANCE = 3;
const MAX_POINTS = 1200;

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
  lastUpdate: number;
  sampleCount: number;
  startedAt: number | null;
  /** Persian sentence for the footer, or null while the first request runs. */
  note: string | null;
  /** Raw failure description, kept for diagnostics. */
  error: string | null;
};

type LivePayload = {
  server_time_ms?: number;
  run?: {started_at_ms?: number; sample_count?: number} | null;
  latest?: {
    id?: number;
    received_at_ms?: number;
    temps?: (number | null)[];
    faults?: number[];
  } | null;
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

export function useTemperatureFeed(): TemperatureFeed {
  const [feed, setFeed] = useState<TemperatureFeed>(emptyFeed);
  const lastSampleId = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    let failures = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const schedule = (delay: number) => {
      if (cancelled) {
        return;
      }
      timer = setTimeout(poll, delay);
    };

    const applyLive = (
      payload: LivePayload,
      temps: (number | null)[],
      faults: number[],
      at: number,
    ) => {
      const sampleId =
        typeof payload.latest?.id === 'number' ? payload.latest.id : null;
      const fresh = sampleId === null || sampleId !== lastSampleId.current;
      lastSampleId.current = sampleId ?? lastSampleId.current;

      setFeed(previous => {
        const channels = previous.channels.map((channel, index) => {
          const raw = temps[index];
          const value =
            typeof raw === 'number' && Number.isFinite(raw) ? round1(raw) : null;

          // Keep the curve continuous. History is never discarded on a
          // reconnect: the controller sends a burst, then pauses for a few
          // seconds, and clearing the series made the chart look like it kept
          // dropping and coming back.
          let history = channel.history;
          if (value !== null) {
            history = fresh
              ? history.concat({t: at, v: value}).slice(-MAX_POINTS)
              : history
                  .slice(0, -1)
                  .concat({t: history[history.length - 1]?.t ?? at, v: value});
          }

          return {
            ...channel,
            temp: value,
            fault: typeof faults[index] === 'number' ? faults[index] : 0,
            history,
            delta: deltaOf(history),
            updatedAt: at,
          };
        });

        const startedAt = Number(payload.run?.started_at_ms);
        const sampleCount = Number(payload.run?.sample_count);

        return {
          source: 'live',
          channels,
          lastUpdate: at,
          sampleCount: Number.isFinite(sampleCount) ? sampleCount : previous.sampleCount,
          startedAt:
            Number.isFinite(startedAt) && startedAt > 0 ? startedAt : previous.startedAt,
          note: null,
          error: null,
        };
      });
    };

    const poll = async () => {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

      try {
        const response = await fetch(API_URL, {
          signal: controller.signal,
          headers: {Accept: 'application/json', 'Cache-Control': 'no-cache'},
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const payload = (await response.json()) as LivePayload;
        const latest = payload?.latest;

        if (!latest || !Array.isArray(latest.temps)) {
          throw new Error('پاسخ سرور خوانا نبود');
        }

        if (!cancelled) {
          const at = Number(payload.server_time_ms) || Date.now();
          failures = 0;
          applyLive(
            payload,
            latest.temps,
            Array.isArray(latest.faults) ? latest.faults : [],
            at,
          );
          schedule(POLL_MS);
        }
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
