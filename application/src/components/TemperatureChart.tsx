import React, {useEffect, useMemo, useRef} from 'react';
import {Animated, Easing, StyleSheet, Text, View} from 'react-native';
import Svg, {
  Circle,
  Defs,
  LinearGradient,
  Line,
  Path,
  Rect,
  Stop,
  Text as SvgText,
} from 'react-native-svg';

import type {Point} from '../data/feed';
import {colors, radius, space, tabular} from '../theme';
import {formatClockShort} from '../utils/format';
import {smoothPath} from './Sparkline';

const AnimatedPath = Animated.createAnimatedComponent(Path);

export type ChartSeries = {
  id: number;
  name: string;
  color: string;
  points: Point[];
};

type Props = {
  series: ChartSeries[];
  windowSec: number;
  width: number;
  height?: number;
  now: number;
};

const PADDING = {top: 16, right: 12, bottom: 24, left: 40};

/**
 * Multi-channel area chart drawn with react-native-svg. Smooth curves, per
 * series gradients and an animated stroke reveal on every window change.
 */
export function TemperatureChart({
  series,
  windowSec,
  width,
  height = 190,
  now,
}: Props) {
  const draw = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    draw.setValue(0);
    Animated.timing(draw, {
      toValue: 1,
      duration: 900,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: false,
    }).start();
  }, [draw, windowSec]);

  const plotWidth = Math.max(10, width - PADDING.left - PADDING.right);
  const plotHeight = Math.max(10, height - PADDING.top - PADDING.bottom);

  const model = useMemo(() => {
    const from = now - windowSec * 1000;
    const visible = series
      .map(line => ({...line, points: line.points.filter(point => point.t >= from)}))
      .filter(line => line.points.length > 0);

    const values = visible.flatMap(line => line.points.map(point => point.v));

    if (values.length === 0) {
      return null;
    }

    let min = Math.min(...values);
    let max = Math.max(...values);

    if (max - min < 8) {
      const middle = (max + min) / 2;
      min = middle - 4;
      max = middle + 4;
    }

    const cushion = (max - min) * 0.16;
    min -= cushion;
    max += cushion;

    const span = max - min || 1;
    const xOf = (t: number) =>
      PADDING.left + ((t - from) / Math.max(1, windowSec * 1000)) * plotWidth;
    const yOf = (v: number) =>
      PADDING.top + (1 - (v - min) / span) * plotHeight;

    const lines = visible.map(line => {
      const points = line.points.map(point => ({x: xOf(point.t), y: yOf(point.v)}));
      const path = smoothPath(points);
      const area = `${path} L${points[points.length - 1].x.toFixed(2)},${(
        PADDING.top + plotHeight
      ).toFixed(2)} L${points[0].x.toFixed(2)},${(
        PADDING.top + plotHeight
      ).toFixed(2)} Z`;

      let length = 0;
      for (let index = 1; index < points.length; index += 1) {
        const dx = points[index].x - points[index - 1].x;
        const dy = points[index].y - points[index - 1].y;
        length += Math.sqrt(dx * dx + dy * dy);
      }

      return {
        ...line,
        path,
        area,
        length: Math.max(1, length),
        last: points[points.length - 1],
      };
    });

    const ticks = [0, 0.5, 1].map(ratio => {
      const value = max - ratio * (max - min);
      return {
        y: PADDING.top + ratio * plotHeight,
        value,
      };
    });

    return {lines, ticks};
  }, [now, plotHeight, plotWidth, series, windowSec]);

  if (!model) {
    return (
      <View style={[styles.empty, {width, height}]}>
        <Text style={styles.emptyText}>در انتظار نخستین نمونه…</Text>
      </View>
    );
  }

  return (
    <View style={{width, height}}>
      <Svg width={width} height={height}>
        <Defs>
          {model.lines.map(line => (
            <LinearGradient
              key={`fill-${line.id}`}
              id={`fill-${line.id}`}
              x1="0"
              y1="0"
              x2="0"
              y2="1">
              <Stop offset="0" stopColor={line.color} stopOpacity={0.28} />
              <Stop offset="0.7" stopColor={line.color} stopOpacity={0.04} />
              <Stop offset="1" stopColor={line.color} stopOpacity={0} />
            </LinearGradient>
          ))}
        </Defs>

        <Rect
          x={PADDING.left}
          y={PADDING.top}
          width={plotWidth}
          height={plotHeight}
          rx={10}
          fill="rgba(124, 176, 255, 0.03)"
        />

        {model.ticks.map(tick => (
          <React.Fragment key={`grid-${tick.y}`}>
            <Line
              x1={PADDING.left}
              y1={tick.y}
              x2={PADDING.left + plotWidth}
              y2={tick.y}
              stroke="rgba(124, 176, 255, 0.12)"
              strokeWidth={1}
              strokeDasharray="3 6"
            />
            <SvgText
              x={PADDING.left - 8}
              y={tick.y + 3.5}
              fill={colors.muted}
              fontSize={9.5}
              textAnchor="end">
              {tick.value.toFixed(0)}
            </SvgText>
          </React.Fragment>
        ))}

        {model.lines.map(line => (
          <Path key={`area-${line.id}`} d={line.area} fill={`url(#fill-${line.id})`} />
        ))}

        {model.lines.map(line => {
          const dashoffset = draw.interpolate({
            inputRange: [0, 1],
            outputRange: [line.length, 0],
          });

          return (
            <AnimatedPath
              key={`line-${line.id}`}
              d={line.path}
              stroke={line.color}
              strokeWidth={2.2}
              strokeLinecap="round"
              strokeLinejoin="round"
              fill="none"
              strokeDasharray={`${line.length} ${line.length}`}
              strokeDashoffset={dashoffset as unknown as number}
            />
          );
        })}

        {model.lines.map(line => (
          <React.Fragment key={`dot-${line.id}`}>
            <Circle
              cx={line.last.x}
              cy={line.last.y}
              r={7}
              fill={line.color}
              fillOpacity={0.16}
            />
            <Circle cx={line.last.x} cy={line.last.y} r={3.1} fill={line.color} />
          </React.Fragment>
        ))}

        <SvgText
          x={PADDING.left}
          y={height - 6}
          fill={colors.muted}
          fontSize={9.5}
          textAnchor="start">
          {formatClockShort(now - windowSec * 1000)}
        </SvgText>
        <SvgText
          x={PADDING.left + plotWidth}
          y={height - 6}
          fill={colors.muted}
          fontSize={9.5}
          textAnchor="end">
          {formatClockShort(now)}
        </SvgText>
      </Svg>
    </View>
  );
}

const styles = StyleSheet.create({
  empty: {
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radius.md,
    backgroundColor: 'rgba(124, 176, 255, 0.04)',
  },
  emptyText: {
    ...tabular,
    color: colors.muted,
    fontSize: 12,
    marginTop: space(2),
  },
});
