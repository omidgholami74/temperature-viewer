import React, {useMemo} from 'react';
import Svg, {Defs, LinearGradient, Path, Stop} from 'react-native-svg';

type Props = {
  values: number[];
  color: string;
  width?: number;
  height?: number;
  strokeWidth?: number;
};

type Pt = {x: number; y: number};

export const smoothPath = (points: Pt[]): string => {
  if (points.length === 0) {
    return '';
  }

  if (points.length === 1) {
    const only = points[0];
    return `M${only.x.toFixed(2)},${only.y.toFixed(2)} L${only.x.toFixed(2)},${only.y.toFixed(2)}`;
  }

  let path = `M${points[0].x.toFixed(2)},${points[0].y.toFixed(2)}`;

  for (let index = 1; index < points.length; index += 1) {
    const previous = points[index - 1];
    const current = points[index];
    const midX = (previous.x + current.x) / 2;
    path += ` C${midX.toFixed(2)},${previous.y.toFixed(2)} ${midX.toFixed(2)},${current.y.toFixed(2)} ${current.x.toFixed(2)},${current.y.toFixed(2)}`;
  }

  return path;
};

/** Tiny inline trend line used inside the channel cards. */
export function Sparkline({
  values,
  color,
  width = 76,
  height = 34,
  strokeWidth = 1.9,
}: Props) {
  const geometry = useMemo(() => {
    if (values.length < 2) {
      return null;
    }

    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const points: Pt[] = values.map((value, index) => ({
      x: (index / (values.length - 1)) * width,
      y: height - 3 - ((value - min) / span) * (height - 6),
    }));

    return {
      line: smoothPath(points),
      area: `${smoothPath(points)} L${width},${height} L0,${height} Z`,
    };
  }, [height, values, width]);

  if (!geometry) {
    return null;
  }

  return (
    <Svg width={width} height={height}>
      <Defs>
        <LinearGradient id="spark-fill" x1="0" y1="0" x2="0" y2="1">
          <Stop offset="0" stopColor={color} stopOpacity={0.35} />
          <Stop offset="1" stopColor={color} stopOpacity={0} />
        </LinearGradient>
      </Defs>
      <Path d={geometry.area} fill="url(#spark-fill)" />
      <Path
        d={geometry.line}
        stroke={color}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
      />
    </Svg>
  );
}
