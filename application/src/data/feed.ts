import {useEffect, useRef, useState} from 'react';

import {CHANNELS} from './channels';
import {clamp, round1} from '../utils/format';

/**
 * Live temperature feed.
 *
 * The app prefers the production API and silently falls back to a realistic
 * local simulation when the server cannot be reached, so the dashboard is
 * always readable on a phone. The active source is shown in the UI.
 */
export const API_BASE = 'https://apitemp.futurefounderco.com';

const POLL_MS = 1500;
const REQUEST_TIMEOUT_MS = 5000;
const MAX_POINTS = 720;
const DEMO_RETRY_MS = 30000;

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

export type FeedSource = 'connecting' | 'live' | 'demo';

export type TemperatureFeed = {
  source: FeedSource;
  channels: ChannelReading[];
  lastUpdate: number;
  sampleCount: number;
  startedAt: number | null;
  note: string | null;
};

type SimState = {value: number; target: number; phase: number};

const createSim = (): SimState[] =>
  CHANNELS.map((channel, index) => ({
    value: channel.baseTemp + Math.sin(index * 1.7) * 8,
    target: channel.baseTemp + (index % 2 === 0 ? 45 : -30),
    phase: Math.random() * Math.PI * 2,
  }));

/** Advances the simulated furnaces by `seconds` and returns their readings. */
const stepSim = (states: SimState[], seconds: number): number[] => {
  const dt = clamp(seconds, 0.2, 5);

  return states.map((state, index) => {
    const channel = CHANNELS[index];
    state.phase += dt * 0.07;

    if (Math.random() < 0.004 * dt) {
      state.target = clamp(
        channel.baseTemp + (Math.random() - 0.5) * 180,
        channel.baseTemp - 130,
        channel.max - 40,
      );
    }

    const pull = (state.target - state.value) * 0.04 * dt;
    const ripple = Math.sin(state.phase) * 1.4 * dt;
    const noise = (Math.random() - 0.5) * 1.1;

    state.value = clamp(state.value + pull + ripple + noise, 24, channel.max);
    return state.value;
  });
};

const deltaOf = (history: Point[]): number => {
  if (history.length < 2) {
    return 0;
  }
  const newest = history[history.length - 1];
  const older = history[Math.max(0, history.length - 7)];
  return round1(newest.v - older.v);
};

const buildChannels = (history: Point[][], updatedAt: number): ChannelReading[] =>
  CHANNELS.map((channel, index) => {
    const points = history[index];
    return {
      id: channel.id,
      name: channel.name,
      color: channel.color,
      temp: points.length > 0 ? points[points.length - 1].v : null,
      fault: 0,
      delta: deltaOf(points),
      history: points,
      updatedAt,
    };
  });

/** A realistic pre-filled buffer so the chart is never empty on first paint. */
const seedFeed = (): TemperatureFeed => {
  const sim = createSim();
  const now = Date.now();
  const history: Point[][] = CHANNELS.map(() => []);

  for (let step = 240; step >= 0; step -= 1) {
    const temps = stepSim(sim, POLL_MS / 1000);
    const t = now - step * POLL_MS;
    temps.forEach((value, index) => {
      history[index].push({t, v: round1(value)});
    });
  }

  return {
    source: 'connecting',
    channels: buildChannels(history, now),
    lastUpdate: now,
    sampleCount: 0,
    startedAt: null,
    note: null,
  };
};

type LivePayload = {
  server_time_ms?: number;
  run?: {started_at_ms?: number; sample_count?: number} | null;
  latest?: {
    temps?: (number | null)[];
    faults?: number[];
  } | null;
};

export function useTemperatureFeed(): TemperatureFeed {
  const [feed, setFeed] = useState<TemperatureFeed>(seedFeed);
  const simRef = useRef<SimState[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    let busy = false;
    let mode: FeedSource = 'connecting';
    let failures = 0;
    let lastAttempt = 0;

    const sim = simRef.current ?? createSim();
    simRef.current = sim;

    const applyLive = (
      temps: (number | null)[],
      faults: number[],
      at: number,
      payload: LivePayload,
    ) => {
      setFeed(previous => {
        const switching = previous.source !== 'live';

        const channels = previous.channels.map((channel, index) => {
          const raw = temps[index];
          const value =
            typeof raw === 'number' && Number.isFinite(raw) ? round1(raw) : null;
          const base = switching ? [] : channel.history;
          const history =
            value === null ? base : base.concat({t: at, v: value}).slice(-MAX_POINTS);

          return {
            ...channel,
            temp: value ?? channel.temp,
            fault: faults[index] ?? 0,
            history,
            delta: deltaOf(history),
            updatedAt: at,
          };
        });

        const startedAt = Number(payload?.run?.started_at_ms);
        const sampleCount = Number(payload?.run?.sample_count);

        return {
          source: 'live',
          channels,
          lastUpdate: at,
          sampleCount: Number.isFinite(sampleCount) ? sampleCount : previous.sampleCount,
          startedAt:
            Number.isFinite(startedAt) && startedAt > 0 ? startedAt : previous.startedAt,
          note: null,
        };
      });
    };

    const pullLive = async (): Promise<boolean> => {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

      try {
        const response = await fetch(`${API_BASE}/api/live`, {
          signal: controller.signal,
          headers: {Accept: 'application/json'},
        });

        if (!response.ok) {
          return false;
        }

        const payload = (await response.json()) as LivePayload;
        const latest = payload?.latest;

        if (!latest || !Array.isArray(latest.temps)) {
          return false;
        }

        const at = Number(payload?.server_time_ms) || Date.now();
        applyLive(
          latest.temps,
          Array.isArray(latest.faults) ? latest.faults : [],
          at,
          payload,
        );

        return true;
      } catch {
        return false;
      } finally {
        clearTimeout(timeout);
      }
    };

    const pushDemo = () => {
      setFeed(previous => {
        const at = Date.now();
        const temps = stepSim(sim, POLL_MS / 1000);

        const channels = previous.channels.map((channel, index) => {
          const history = channel.history
            .concat({t: at, v: round1(temps[index])})
            .slice(-MAX_POINTS);

          return {
            ...channel,
            temp: history[history.length - 1].v,
            fault: 0,
            history,
            delta: deltaOf(history),
            updatedAt: at,
          };
        });

        return {
          ...previous,
          source: 'demo',
          channels,
          lastUpdate: at,
          note: 'اتصال به سرور برقرار نیست — نمایش داده آزمایشی',
        };
      });
    };

    const tick = async () => {
      if (cancelled || busy) {
        return;
      }

      busy = true;

      try {
        const retryDue =
          mode !== 'demo' || Date.now() - lastAttempt > DEMO_RETRY_MS;

        if (retryDue) {
          lastAttempt = Date.now();
          const ok = await pullLive();

          if (ok) {
            mode = 'live';
            failures = 0;
            return;
          }

          failures += 1;
          const threshold = mode === 'connecting' ? 1 : 2;
          if (failures >= threshold) {
            mode = 'demo';
          }
        }

        if (mode === 'demo' && !cancelled) {
          pushDemo();
        }
      } finally {
        busy = false;
      }
    };

    tick();
    const timer = setInterval(tick, POLL_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return feed;
}
